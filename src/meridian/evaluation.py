"""Scores the persisted agent output against hand-labelled gold data (data/gold_labels.csv)."""
import csv
from collections import Counter

from sqlalchemy import func, select

from .config import get_settings
from .db import Action, AgentRun, CourtesyCredit, Request, SessionLocal, ToolCall
from .observability import estimate_cost

CODES = {"B": "Billing & Autopay Dispute", "G": "Gate Access & Lockout", "T": "Unit Transfer & Reservation Change",
         "D": "Damage & Insurance Claim", "L": "Delinquency & Auction Notice"}


def load_gold() -> dict[str, dict]:
    with open(get_settings().data_dir / "gold_labels.csv", encoding="utf-8") as f:
        return {r["request_id"]: r for r in csv.DictReader(f)}


def _actual_action(r: Request, credit: CourtesyCredit | None, fallback: bool) -> str:
    if fallback:
        return "fallback"
    if r.status == "closed_duplicate":
        return "close_duplicate"
    if credit and credit.status in ("issued", "reversed"):
        return "credit"
    if credit and credit.status == "pending_approval":
        return "pending_approval"
    return "route"


def evaluate() -> dict:
    gold = load_gold()
    with SessionLocal() as s:
        reqs = {r.request_id: r for r in s.scalars(select(Request))}
        credits = {c.request_id: c for c in s.scalars(select(CourtesyCredit))}
        runs = list(s.scalars(select(AgentRun)))
        fallback_ids = {a.request_id for a in s.scalars(select(Action).where(Action.action_type == "fallback_route"))}
        blocked = s.scalar(select(func.count()).select_from(ToolCall).where(ToolCall.allowed.is_(False)))
        tool_counts = Counter(t for (t,) in s.execute(select(ToolCall.tool_name).where(ToolCall.allowed.is_(True))))

    cat_ok = pri_ok = act_ok = n = 0
    p1_hit = p1_total = 0
    misses: list[dict] = []
    wrong_credits, correct_credits, missed_credits = [], 0, []
    strict_credit_total = 0
    for rid, g in gold.items():
        if g["expected_action"] == "skip":
            continue
        r = reqs.get(rid)
        if r is None or r.category is None and rid not in fallback_ids:
            misses.append({"request_id": rid, "issue": "not triaged"})
            continue
        n += 1
        credit = credits.get(rid)
        allowed_cats = set(CODES.values()) if g["category"] == "*" else {CODES[c] for c in [g["category"], *filter(None, g["alt_categories"].split("|"))]}
        c_ok = r.category in allowed_cats
        p_ok = r.priority in g["priorities"].split("|")
        actual = _actual_action(r, credit, rid in fallback_ids)
        a_ok = actual in g["expected_action"].split("|")
        cat_ok += c_ok
        pri_ok += p_ok
        act_ok += a_ok
        if g["priorities"] == "P1":
            p1_total += 1
            p1_hit += r.priority == "P1"
        if g["expected_action"] == "credit":
            strict_credit_total += 1
        if credit and credit.status in ("issued", "pending_approval", "reversed"):
            amount_ok = g["credit_amount"] and abs(credit.amount - float(g["credit_amount"])) < 0.005
            allowed = any(x in g["expected_action"] for x in ("credit", "pending_approval"))
            if allowed and amount_ok:
                correct_credits += 1
            else:
                wrong_credits.append({"request_id": rid, "amount": credit.amount, "status": credit.status, "gold": g["expected_action"]})
        elif g["expected_action"] == "credit":
            missed_credits.append(rid)
        if not (c_ok and p_ok and a_ok):
            misses.append({"request_id": rid, "category": r.category, "gold_category": g["category"],
                           "priority": r.priority, "gold_priorities": g["priorities"], "action": actual,
                           "gold_action": g["expected_action"], "note": g["note"]})

    issued = correct_credits + len(wrong_credits)
    tokens_in, tokens_out = sum(r.input_tokens for r in runs), sum(r.output_tokens for r in runs)
    model = runs[0].model if runs else ""
    return {
        "model": model, "evaluated": n,
        "category_accuracy": round(cat_ok / n, 3) if n else 0,
        "priority_accuracy": round(pri_ok / n, 3) if n else 0,
        "action_accuracy": round(act_ok / n, 3) if n else 0,
        "p1_recall": f"{p1_hit}/{p1_total}",
        "credit_precision": round(correct_credits / issued, 3) if issued else 1.0,
        "credit_recall_strict": f"{strict_credit_total - len(missed_credits)}/{strict_credit_total}",
        "wrong_credits": wrong_credits, "missed_credits": missed_credits,
        "fallbacks": len(fallback_ids), "guardrail_blocks": blocked, "tool_usage": dict(tool_counts),
        "avg_steps": round(sum(r.steps for r in runs) / len(runs), 2) if runs else 0,
        "avg_latency_ms": int(sum(r.latency_ms for r in runs) / len(runs)) if runs else 0,
        "tokens": {"input": tokens_in, "output": tokens_out},
        "est_cost_usd": estimate_cost(model, tokens_in, tokens_out),
        "misses": misses,
    }


def print_report(rep: dict) -> None:
    print(f"\n=== Eval: {rep['model']} on {rep['evaluated']} requests ===")
    for k in ("category_accuracy", "priority_accuracy", "action_accuracy", "p1_recall", "credit_precision",
              "credit_recall_strict", "fallbacks", "guardrail_blocks", "avg_steps", "avg_latency_ms", "est_cost_usd"):
        print(f"  {k:22s} {rep[k]}")
    print(f"  tool_usage             {rep['tool_usage']}")
    if rep["wrong_credits"]:
        print("  WRONG CREDITS:", rep["wrong_credits"])
    if rep["missed_credits"]:
        print("  missed credits:", rep["missed_credits"])
    print(f"  misses ({len(rep['misses'])}):")
    for m in rep["misses"]:
        print("   ", m)


def summary() -> dict:
    with SessionLocal() as s:
        rows = list(s.scalars(select(Request)))
        credits = list(s.scalars(select(CourtesyCredit)))
    return {"requests": len(rows),
            "by_status": Counter(r.status for r in rows), "by_priority": Counter(r.priority for r in rows),
            "by_team": Counter(r.team for r in rows), "by_category": Counter(r.category for r in rows),
            "credits": {st: round(sum(c.amount for c in credits if c.status == st), 2) for st in {c.status for c in credits}}}
