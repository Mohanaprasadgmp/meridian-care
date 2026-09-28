# Original path: tests/test_services.py
"""Human override operations are validated and fully audited."""
import pytest
from sqlalchemy import select

from meridian import services
from meridian.agent.orchestrator import triage_all
from meridian.db import CourtesyCredit, Override, Request, SessionLocal


@pytest.fixture()
def triaged(seeded):
    triage_all(workers=1)


def test_override_requires_reviewer_and_reason(triaged):
    with pytest.raises(ValueError):
        services.override_field("TR-6051", "priority", "P1", "", "")


def test_override_rejects_invalid_value(triaged):
    with pytest.raises(ValueError):
        services.override_field("TR-6051", "team", "Marketing", "ana", "x")


def test_override_is_persisted_and_audited(triaged):
    services.override_field("TR-6051", "priority", "P2", "ana", "tenant called in")
    with SessionLocal() as s:
        assert s.get(Request, "TR-6051").priority == "P2"
        o = s.scalar(select(Override).where(Override.request_id == "TR-6051"))
        assert (o.field, o.new_value, o.reviewer) == ("priority", "P2", "ana")


def test_reverse_credit_and_approve_pending(triaged):
    with SessionLocal() as s:
        issued = s.scalar(select(CourtesyCredit).where(CourtesyCredit.request_id == "TR-6037"))
        pending = s.scalar(select(CourtesyCredit).where(CourtesyCredit.request_id == "TR-6070"))
    services.reverse_credit(issued.id, "ana", "duplicate adjustment already applied")
    services.approve_credit(pending.id, "ana", "verified against statement")
    with SessionLocal() as s:
        assert s.get(CourtesyCredit, issued.id).status == "reversed"
        assert s.get(Request, "TR-6037").status == "routed"
        assert s.get(CourtesyCredit, pending.id).status == "issued"
    with pytest.raises(ValueError):
        services.reverse_credit(issued.id, "ana", "again")


def test_create_request_assigns_next_id_and_is_triageable(seeded):
    from meridian.agent.orchestrator import pending_request_ids, triage_request
    rid = services.create_request("  Hollis   Grant ", "Standard", "Water is pooling in my unit and ruining boxes.")
    assert rid == "TR-6101"
    assert services.create_request("A B", "Business Elite", "hello") == "TR-6102"
    assert rid in pending_request_ids()
    with SessionLocal() as s:
        r = s.get(Request, rid)
        assert (r.customer_name, r.status, r.source_status) == ("Hollis Grant", "new", "new")
    assert triage_request(rid)["status"] == "completed"


@pytest.mark.parametrize("name,tier,body", [("", "Standard", "x"), ("A", "Standard", "  "),
                                            ("A", "Gold", "x"), ("A", "Standard", "x" * 5001)])
def test_create_request_validation(seeded, name, tier, body):
    with pytest.raises(ValueError):
        services.create_request(name, tier, body)


def test_reopen_duplicate(triaged):
    services.reopen_duplicate("TR-6039", "ana", "different lock")
    with SessionLocal() as s:
        r = s.get(Request, "TR-6039")
        assert r.status == "routed" and r.duplicate_of is None
