"""Persistence: requests, classifications, agent runs, tool calls, actions, credits, overrides."""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Integer, String, Text, inspect, text,
                        UniqueConstraint, create_engine, event)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Request(Base):
    __tablename__ = "requests"
    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(128), index=True)
    account_tier: Mapped[str] = mapped_column(String(32))
    source_status: Mapped[str] = mapped_column(String(16))       # status as received in the CSV
    status: Mapped[str] = mapped_column(String(24), index=True)  # current workflow status
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    body: Mapped[str] = mapped_column(Text)
    # Current effective triage (agent output, possibly overridden by a human)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    priority: Mapped[Optional[str]] = mapped_column(String(4), index=True)
    priority_score: Mapped[Optional[int]] = mapped_column(Integer)
    team: Mapped[Optional[str]] = mapped_column(String(48), index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    duplicate_of: Mapped[Optional[str]] = mapped_column(String(32))
    human_reviewed: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # Intake channel: "csv" (bulk seed/eval data) or "chat" (raised by a logged-in customer)
    source: Mapped[str] = mapped_column(String(8), default="csv", index=True)
    customer_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(ForeignKey("conversations.id"), index=True)
    intake_summary: Mapped[Optional[str]] = mapped_column(Text)            # Interaction Agent's one-line summary
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    customer_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    """One chat thread. Owned by exactly one customer account: replies are routed by this id."""
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)          # uuid4
    customer_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))   # hidden by the customer;
    # kept (not dropped) because its tickets, messages and audit trail still belong to the admin record


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    sender: Mapped[str] = mapped_column(String(16))                       # "customer" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    request_id: Mapped[Optional[str]] = mapped_column(ForeignKey("requests.request_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillingRecord(Base):
    __tablename__ = "billing_records"
    customer_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    verified_discrepancy: Mapped[float] = mapped_column(Float)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.request_id"), index=True)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))        # completed | fallback | error
    steps: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text)
    final_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Classification(Base):
    __tablename__ = "classifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.request_id"), index=True)
    category: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    urgency_signals: Mapped[list] = mapped_column(JSON)
    language: Mapped[str] = mapped_column(String(8))
    claim_kind: Mapped[str] = mapped_column(String(48))
    claimed_amount: Mapped[Optional[float]] = mapped_column(Float)
    claim_is_specific: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(4))
    priority_score: Mapped[int] = mapped_column(Integer)
    priority_explanation: Mapped[str] = mapped_column(Text)
    team: Mapped[str] = mapped_column(String(48))
    routing_explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    step: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(48))
    arguments: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    allowed: Mapped[bool] = mapped_column(default=True)   # False = blocked by a guardrail
    agent_rationale: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.request_id"), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_runs.id"))
    action_type: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(64))          # "agent" or reviewer name
    details: Mapped[dict] = mapped_column(JSON)
    tenant_message: Mapped[Optional[str]] = mapped_column(Text)
    reverted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CourtesyCredit(Base):
    __tablename__ = "courtesy_credits"
    __table_args__ = (UniqueConstraint("request_id", name="uq_credit_request"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.request_id"), index=True)
    customer_name: Mapped[str] = mapped_column(String(128), index=True)
    amount: Mapped[float] = mapped_column(Float)
    claimed_amount: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24))   # issued | pending_approval | reversed | rejected
    decided_by: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Override(Base):
    __tablename__ = "overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.request_id"), index=True)
    field: Mapped[str] = mapped_column(String(32))
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    """Console login. Only a salted PBKDF2 hash is stored, never the password."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="admin")        # "admin" | "customer"
    display_name: Mapped[Optional[str]] = mapped_column(String(128))       # customers: tenant name (billing match)
    account_tier: Mapped[Optional[str]] = mapped_column(String(32))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
_Session = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_settings().database_url
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
    return _engine


def SessionLocal():
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _Session()


# Backfills for columns added after a database was first created (existing rows would otherwise be NULL)
_BACKFILL = {("requests", "source"): "'csv'", ("users", "role"): "'admin'"}


def _migrate(engine) -> None:
    """Additive, idempotent migration: ADD COLUMN for any model column missing from an existing table."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}'))
                if (table.name, col.name) in _BACKFILL:
                    conn.execute(text(f"UPDATE {table.name} SET {col.name} = {_BACKFILL[(table.name, col.name)]} "
                                      f"WHERE {col.name} IS NULL"))


def init_db(reset: bool = False) -> None:
    engine = get_engine()
    if reset:  # wipes triage and chat data; login accounts survive a data reset
        Base.metadata.drop_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name != "users"])
    Base.metadata.create_all(engine)
    _migrate(engine)


def reset_engine() -> None:
    """Used by tests to point at a fresh database."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None
