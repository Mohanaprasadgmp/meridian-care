"""Background worker: the hand-off between the two agents, through the database (a durable queue).

  new chat request --claim--> Triage Agent --outcome--> Interaction Agent --reply--> customer's conversation

Every step is claimed with a conditional UPDATE, so any number of worker threads/processes/replicas can run
side by side: each request is triaged once and each customer is notified once. Nothing is held in memory,
so a crash loses nothing: stale claims are re-queued and un-notified outcomes are picked up on the next poll.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Optional

from sqlalchemy import select, update

from .. import chat
from ..config import Settings, get_settings
from ..db import AgentRun, Request, SessionLocal, utcnow
from ..observability import log
from .interaction import compose_reply
from .orchestrator import triage_request

TERMINAL = ("routed", "resolved", "closed_duplicate", "needs_review", "acknowledged", "closed", "awaiting_customer")


def requeue_stale(settings: Settings) -> int:
    cutoff = utcnow() - timedelta(seconds=settings.processing_timeout_s)
    with SessionLocal() as s:
        stale = s.scalars(select(Request.request_id).where(Request.status == "processing",
                                                           Request.claimed_at < cutoff)).all()
        for rid in stale:
            s.execute(update(AgentRun).where(AgentRun.request_id == rid, AgentRun.status == "running")
                      .values(status="superseded", error="worker timed out; re-queued"))
            s.execute(update(Request).where(Request.request_id == rid, Request.status == "processing")
                      .values(status=Request.source_status, claimed_at=None))
        s.commit()
    if stale:
        log.warning("worker.requeued", extra={"count": len(stale)})
    return len(stale)


def pending_ids(settings: Settings, limit: int) -> list[str]:
    sources = [x.strip() for x in settings.worker_sources.split(",") if x.strip()]
    with SessionLocal() as s:
        return list(s.scalars(select(Request.request_id)
                              .where(Request.status.in_(["new", "open"]), Request.source.in_(sources))
                              .order_by(Request.submitted_at).limit(limit)))


def notify_pending(settings: Settings, llm=None) -> int:
    """Deliver a reply for every triaged chat request whose customer hasn't been told yet (exactly once)."""
    with SessionLocal() as s:
        ids = s.scalars(select(Request.request_id).where(
            Request.source == "chat", Request.conversation_id.is_not(None),
            Request.customer_notified_at.is_(None), Request.status.in_(TERMINAL))).all()
    sent = 0
    for rid in ids:
        with SessionLocal() as s:     # claim the notification
            won = s.execute(update(Request).where(Request.request_id == rid, Request.customer_notified_at.is_(None))
                            .values(customer_notified_at=utcnow())).rowcount == 1
            conv = s.get(Request, rid).conversation_id
            s.commit()
        if not won:
            continue
        try:
            chat.deliver_system_reply(conv, compose_reply(rid, llm=llm, settings=settings), rid)
            sent += 1
        except Exception:
            log.exception("worker.notify_failed", extra={"request_id": rid})
            with SessionLocal() as s:  # release the claim so the next poll retries
                s.execute(update(Request).where(Request.request_id == rid).values(customer_notified_at=None))
                s.commit()
    return sent


def run_once(settings: Optional[Settings] = None, llm=None) -> dict:
    settings = settings or get_settings()
    requeue_stale(settings)
    ids = pending_ids(settings, limit=settings.worker_threads * 2)
    if ids:
        with ThreadPoolExecutor(max_workers=settings.worker_threads) as pool:
            list(pool.map(lambda rid: triage_request(rid, llm=llm, settings=settings), ids))
    return {"triaged": len(ids), "notified": notify_pending(settings, llm=llm)}


class Worker:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="meridian-worker", daemon=True)

    def _loop(self) -> None:
        log.info("worker.started", extra={"poll_s": self.settings.worker_poll_s, "sources": self.settings.worker_sources})
        while not self._stop.is_set():
            try:
                run_once(self.settings)
            except Exception:  # keep the loop alive; the failure is logged and retried next poll
                log.exception("worker.loop_error")
            self._stop.wait(self.settings.worker_poll_s)

    def start(self) -> "Worker":
        self.thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
