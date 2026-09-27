"""Bounded agent loop: LLM -> tool calls -> guarded execution -> results -> LLM ... -> one final action.

Failure policy: any error, refusal, step exhaustion or missing final action degrades to
"route to Human Triage" - a request is never dropped and never auto-actioned on a failure path.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from ..config import Settings, get_settings
from ..db import Action, AgentRun, Request, SessionLocal, ToolCall
from ..llm import LLMClient, get_llm
from ..models import ActionType, RequestStatus, Team
from ..observability import estimate_cost, log
from ..tools.definitions import TOOLS
from ..tools.environment import ToolEnvironment

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(version: str) -> str:
    return (PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")


def render_request(r: Request) -> str:
    # Metadata is trusted (from our DB); the body is untrusted tenant text, fenced and labelled as data.
    return (f"Triage this request.\n\nrequest_id: {r.request_id}\ncustomer_name: {r.customer_name}\n"
            f"account_tier: {r.account_tier}\nsubmitted_at: {r.submitted_at.isoformat()}\n"
            f"current_status: {r.status}\n\n<tenant_request>\n{r.body}\n</tenant_request>")


def triage_request(request_id: str, llm: Optional[LLMClient] = None, settings: Optional[Settings] = None) -> dict:
    settings = settings or get_settings()
    llm = llm or get_llm(settings.llm_provider)
    system = load_prompt(settings.prompt_version)
    started = time.perf_counter()

    with SessionLocal() as s:
        req = s.get(Request, request_id)
        run = AgentRun(request_id=request_id, model=llm.model, prompt_version=settings.prompt_version, status="running")
        s.add(run)
        # Commit per step, never hold a write transaction across an LLM call: SQLite has a single writer,
        # so an open transaction would serialise every worker. Also makes the trace durable step by step.
        s.commit()
        env = ToolEnvironment(session=s, request=req, run_id=run.id, settings=settings)
        messages: list[dict] = [{"role": "user", "content": render_request(req)}]
        step, error, final_text = 0, None, None
        try:
            while step < settings.max_agent_steps:
                step += 1
                resp = llm.complete(system, messages, TOOLS)
                run.input_tokens += resp.input_tokens
                run.output_tokens += resp.output_tokens
                if resp.stop_reason == "refusal":
                    error = "model refusal"
                    break
                messages.append({"role": "assistant", "content": resp.raw_content})
                if not resp.tool_uses:
                    final_text = resp.text
                    break
                results = []
                for tu in resp.tool_uses:
                    result, allowed = env.dispatch(tu["name"], tu["input"])
                    s.add(ToolCall(run_id=run.id, step=step, tool_name=tu["name"], arguments=tu["input"],
                                   result=result, allowed=allowed, agent_rationale=(tu["input"] or {}).get("rationale")))
                    results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": json.dumps(result, default=str),
                                    **({"is_error": True} if not allowed else {})})
                    if not allowed:
                        log.info("guardrail.blocked", extra={"request_id": request_id, "tool": tu["name"], "reason": result.get("error")})
                messages.append({"role": "user", "content": results})   # all results in ONE message
                s.commit()
            else:
                error = f"step budget ({settings.max_agent_steps}) exhausted"
        except Exception as e:  # provider/network/unexpected: degrade safely
            error = f"{type(e).__name__}: {e}"
            log.exception("agent.error", extra={"request_id": request_id})

        if env.terminal_action is None:
            _fallback(s, env, error or "agent finished without a final action")
            run.status = "fallback"
        else:
            run.status = "completed"
        run.steps, run.error, run.final_message = step, error, final_text
        run.latency_ms = int((time.perf_counter() - started) * 1000)
        s.commit()
        out = {"request_id": request_id, "status": run.status, "steps": step, "priority": req.priority,
               "team": req.team, "category": req.category, "request_status": req.status,
               "cost_usd": estimate_cost(llm.model, run.input_tokens, run.output_tokens), "latency_ms": run.latency_ms}
        log.info("agent.run", extra=out)
        return out


def _fallback(s, env: ToolEnvironment, reason: str) -> None:
    r, t = env.request, env.triage
    if t:
        r.category, r.confidence, r.priority, r.priority_score = (t.classification.category.value, t.classification.confidence,
                                                                  t.priority.value, t.priority_score)
    r.team, r.status = Team.HUMAN_TRIAGE.value, RequestStatus.NEEDS_REVIEW.value
    s.add(Action(request_id=r.request_id, run_id=env.run_id, action_type=ActionType.FALLBACK_ROUTE.value, actor="system",
                 details={"reason": reason, "team": Team.HUMAN_TRIAGE.value}))


def pending_request_ids(limit: Optional[int] = None) -> list[str]:
    with SessionLocal() as s:
        done = select(AgentRun.request_id).where(AgentRun.status.in_(["completed", "fallback"]))
        q = (select(Request.request_id).where(Request.status.in_(["new", "open"]), Request.request_id.not_in(done))
             .order_by(Request.submitted_at))
        ids = list(s.scalars(q))
    return ids[:limit] if limit else ids


def requeue_failed() -> list[str]:
    """Re-queue requests whose latest run fell back because of a provider/runtime error (e.g. rate limit).

    Model-behaviour fallbacks (refusal, step budget, no final action) and anything a human already
    touched are left alone. Old runs are kept for the audit trail and marked 'superseded'.
    """
    requeued = []
    with SessionLocal() as s:
        for r in s.scalars(select(Request).where(Request.status == RequestStatus.NEEDS_REVIEW.value,
                                                 Request.human_reviewed.is_(False))):
            last = s.scalar(select(AgentRun).where(AgentRun.request_id == r.request_id).order_by(AgentRun.id.desc()))
            if not (last and last.status == "fallback" and last.error and "Error" in last.error.split(":")[0]):
                continue
            for run in s.scalars(select(AgentRun).where(AgentRun.request_id == r.request_id,
                                                        AgentRun.status.in_(["fallback", "running"]))):
                run.status = "superseded"
            r.status = r.source_status
            r.category = r.priority = r.team = r.confidence = r.priority_score = None
            requeued.append(r.request_id)
        s.commit()
    return requeued


def triage_all(limit: Optional[int] = None, workers: Optional[int] = None) -> list[dict]:
    settings = get_settings()
    ids = pending_request_ids(limit)
    workers = workers or settings.workers
    if workers <= 1 or settings.llm_provider == "fake":
        return [triage_request(i, settings=settings) for i in ids]
    out = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(triage_request, i, None, settings): i for i in ids}
        for f in as_completed(futures):
            out.append(f.result())
    return out
