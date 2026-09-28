# Original path: tests/test_chat.py
"""Customer chat + Customer Interaction Agent + worker hand-off (offline agent)."""
import threading

import pytest
from sqlalchemy import select

from meridian import auth, chat
from meridian.agent import interaction, worker
from meridian.agent.orchestrator import triage_request
from meridian.config import get_settings
from meridian.db import BillingRecord, ChatMessage, Request, SessionLocal


def make_customer(username, display_name, tier="Standard"):
    auth.upsert_user(username, "pass-12345", role="customer", display_name=display_name, account_tier=tier)
    ok, _, user = auth.login(username, "pass-12345", role="customer")
    assert ok
    return user


def new_chat(user):
    return chat.start_conversation(user["id"], greeting=interaction.greeting(user["display_name"]))


@pytest.fixture()
def billing(fresh_db):
    with SessionLocal() as s:
        s.add(BillingRecord(customer_name="Tessa Alder", verified_discrepancy=22.40))
        s.commit()


# ---------------------------------------------------------------- login per portal
def test_login_is_scoped_to_portal(fresh_db):
    auth.upsert_user("boss", "pass-12345", role="admin")
    make_customer("tessa", "Tessa Alder")
    assert auth.login("boss", "pass-12345", "admin")[0] and not auth.login("boss", "pass-12345", "customer")[0]
    assert auth.login("tessa", "pass-12345", "customer")[0] and not auth.login("tessa", "pass-12345", "admin")[0]
    assert auth.login("tessa", "pass-12345", "admin")[1] == "Invalid username or password."


def test_customer_account_needs_tenant_name(fresh_db):
    with pytest.raises(ValueError):
        auth.upsert_user("x", "pass-12345", role="customer")


# ---------------------------------------------------------------- intake
def test_issue_becomes_new_chat_request_with_account_identity(billing):
    u = make_customer("tessa", "Tessa Alder")
    conv = new_chat(u)
    text = "Hi, I'm actually Percival Ashdown. My autopay this month was $22.40 more than my usual rent."
    out = interaction.handle_customer_message(u, conv, text)
    with SessionLocal() as s:
        r = s.get(Request, out["request_id"])
    assert (r.status, r.source, r.conversation_id, r.customer_user_id) == ("new", "chat", conv, u["id"])
    assert r.customer_name == "Tessa Alder"          # from the account, not from what the customer typed
    assert r.body == text                             # customer's own words, verbatim
    assert out["request_id"] in out["reply"]


def test_greeting_and_vague_messages_do_not_create_tickets(fresh_db):
    u = make_customer("tessa", "Tessa Alder")
    conv = new_chat(u)
    interaction.handle_customer_message(u, conv, "hello")
    interaction.handle_customer_message(u, conv, "I have a problem")
    with SessionLocal() as s:
        assert s.scalar(select(Request)) is None
    assert len(chat.get_messages(u["id"], conv)) == 5   # greeting + 2 customer + 2 assistant


def test_message_limits(fresh_db):
    u = make_customer("tessa", "Tessa Alder")
    conv = new_chat(u)
    with pytest.raises(ValueError):
        interaction.handle_customer_message(u, conv, "   ")
    with pytest.raises(ValueError):
        interaction.handle_customer_message(u, conv, "x" * (get_settings().chat_max_chars + 1))


def test_rate_limit_blocks_ticket_flooding(fresh_db, monkeypatch):
    from meridian import config
    monkeypatch.setenv("MERIDIAN_CHAT_MAX_REQUESTS_PER_HOUR", "1")
    config.get_settings.cache_clear()
    u = make_customer("tessa", "Tessa Alder")
    conv = new_chat(u)
    assert interaction.handle_customer_message(u, conv, "The gate keypad rejects my code every time")["request_id"]
    other = new_chat(u)                               # separate conversation, so it's the rate limit that blocks it
    assert interaction.handle_customer_message(u, other, "My unit door lock is jammed today")["request_id"] is None
    config.get_settings.cache_clear()


def test_llm_failure_still_logs_the_issue(fresh_db):
    class Boom:
        model = "boom"

        def complete(self, *a):
            raise TimeoutError("provider down")
    u = make_customer("tessa", "Tessa Alder")
    conv = new_chat(u)
    out = interaction.handle_customer_message(u, conv, "Water is leaking into my unit right now", llm=Boom())
    assert out["request_id"] and out["request_id"] in out["reply"]


# ---------------------------------------------------------------- ownership / routing
def test_customers_cannot_read_or_write_each_others_conversations(fresh_db):
    a, b = make_customer("tessa", "Tessa Alder"), make_customer("omar", "Omar Reyes")
    conv_a = new_chat(a)
    with pytest.raises(chat.NotYourConversation):
        chat.get_messages(b["id"], conv_a)
    with pytest.raises(chat.NotYourConversation):
        interaction.handle_customer_message(b, conv_a, "Let me post into someone else's chat please")
    assert [c["id"] for c in chat.list_conversations(b["id"])] == []


def test_two_customers_interleaved_each_get_only_their_own_answer(billing):
    a, b = make_customer("tessa", "Tessa Alder"), make_customer("omar", "Omar Reyes")
    ca, cb = new_chat(a), new_chat(b)
    ra = interaction.handle_customer_message(a, ca, "My autopay charge was $22.40 more than my usual rent, please fix it")
    rb = interaction.handle_customer_message(b, cb, "My gate code stopped working this morning and I cannot get in")
    worker.run_once()
    ma, mb = chat.get_messages(a["id"], ca), chat.get_messages(b["id"], cb)
    final_a, final_b = ma[-1], mb[-1]
    assert final_a["request_id"] == ra["request_id"] and "$22.40" in final_a["content"]
    assert final_b["request_id"] == rb["request_id"] and rb["request_id"] in final_b["content"]
    assert not any(rb["request_id"] in m["content"] for m in ma)
    assert not any(ra["request_id"] in m["content"] or "$22.40" in m["content"] for m in mb)


# ---------------------------------------------------------------- worker: exactly once
def test_worker_triages_and_notifies_exactly_once(billing):
    u = make_customer("tessa", "Tessa Alder")
    conv = new_chat(u)
    interaction.handle_customer_message(u, conv, "My autopay charge was $22.40 more than my usual rent")
    worker.run_once()
    worker.run_once()
    with SessionLocal() as s:
        replies = s.scalars(select(ChatMessage).where(ChatMessage.conversation_id == conv,
                                                      ChatMessage.sender == "assistant")).all()
    assert len(replies) == 3                     # greeting + ack + one outcome reply (not two)


def test_concurrent_triage_claims_each_request_once(billing):
    u = make_customer("tessa", "Tessa Alder")
    rid = interaction.handle_customer_message(u, new_chat(u), "My gate code stopped working this morning")["request_id"]
    results = []
    threads = [threading.Thread(target=lambda: results.append(triage_request(rid)["status"])) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results).count("skipped") == 3 and len(results) == 4


def test_worker_leaves_csv_requests_alone_by_default(seeded):
    worker.run_once()
    with SessionLocal() as s:
        assert {r.status for r in s.scalars(select(Request).where(Request.source == "csv"))} <= {"new", "open", "closed"}


def test_stale_claim_is_requeued(fresh_db):
    from datetime import timedelta
    from meridian.db import utcnow
    u = make_customer("tessa", "Tessa Alder")
    rid = interaction.handle_customer_message(u, new_chat(u), "My gate code stopped working this morning")["request_id"]
    with SessionLocal() as s:
        r = s.get(Request, rid)
        r.status, r.claimed_at = "processing", utcnow() - timedelta(hours=1)
        s.commit()
    assert worker.requeue_stale(get_settings()) == 1
    with SessionLocal() as s:
        assert s.get(Request, rid).status == "new"


# ---------------------------------------------------------------- output guardrails
def test_outcome_reply_cannot_mention_unissued_money():
    outcome = {"request_id": "TR-1", "final_action": "routed", "credit": {"amount": 308.6, "status": "pending_approval"}}
    assert interaction.guard_outcome_reply("We refunded $308.60 to you. TR-1", outcome) is None
    assert interaction.guard_outcome_reply("We will refund you soon. TR-1", outcome) is None
    ok = interaction.guard_outcome_reply("Your billing team will follow up.", outcome)
    assert ok and "TR-1" in ok


def test_outcome_reply_allows_exact_issued_credit():
    outcome = {"request_id": "TR-2", "final_action": "resolved_with_credit", "credit": {"amount": 22.4, "status": "issued"}}
    assert interaction.guard_outcome_reply("We applied a $22.40 credit (TR-2).", outcome)
    assert interaction.guard_outcome_reply("We applied a $50.00 credit (TR-2).", outcome) is None


def test_intake_reply_cannot_promise_money():
    assert interaction.guard_intake_reply("Don't worry, we will refund you.", set(), "TR-3") is None
    assert interaction.guard_intake_reply("I've noted the $22.40 difference.", {22.40}, "TR-3")
    assert interaction.guard_intake_reply("You'll get $99 back.", {22.40}, "TR-3") is None


# ---------------------------------------------------------------- smart agent: offer / KB / notes
@pytest.fixture()
def santhosh(fresh_db):
    with SessionLocal() as s:
        s.add(BillingRecord(customer_name="Santhosh", verified_discrepancy=20.00))
        s.commit()
    return make_customer("santhosh", "Santhosh")


def _ask(user, conv, text):
    out = interaction.handle_customer_message(user, conv, text)
    worker.run_once()
    return out, chat.get_messages(user["id"], conv)[-1]["content"]


def test_over_claim_gets_offer_then_yes_issues_verified_amount(santhosh):
    from meridian.db import CourtesyCredit
    conv = new_chat(santhosh)
    out, last = _ask(santhosh, conv, "My autopay charge this month was $30 more than my usual rent, please fix it")
    with SessionLocal() as s:
        assert s.get(Request, out["request_id"]).status == "awaiting_customer"
        assert s.scalar(select(CourtesyCredit)).status == "offered"
    assert "$20.00" in last and "yes or no" in last.lower()
    reply = interaction.handle_customer_message(santhosh, conv, "Yes please")["reply"]
    with SessionLocal() as s:
        c = s.scalar(select(CourtesyCredit))
        assert (c.status, c.amount, c.claimed_amount) == ("issued", 20.00, 30.0)
        assert s.get(Request, out["request_id"]).status == "resolved"
    assert "$20.00" in reply


def test_over_claim_declined_goes_to_billing(santhosh):
    from meridian.db import CourtesyCredit
    conv = new_chat(santhosh)
    out, _ = _ask(santhosh, conv, "I was overcharged $40 on my storage bill this cycle, please refund the difference.")
    interaction.handle_customer_message(santhosh, conv, "No, that's wrong, it was definitely $40")
    with SessionLocal() as s:
        assert s.scalar(select(CourtesyCredit)).status == "declined"
        r = s.get(Request, out["request_id"])
        assert (r.status, r.team) == ("routed", "Billing Team")


def test_offer_is_revalidated_when_customer_accepts(santhosh):
    from meridian.db import CourtesyCredit
    conv = new_chat(santhosh)
    _ask(santhosh, conv, "My autopay charge this month was $30 more than my usual rent, please fix it")
    with SessionLocal() as s:                     # billing record corrected before the customer answers
        s.get(BillingRecord, "Santhosh").verified_discrepancy = 5.00
        s.commit()
    interaction.handle_customer_message(santhosh, conv, "yes")
    with SessionLocal() as s:
        assert s.scalar(select(CourtesyCredit)).status == "rejected"


def test_csv_over_claim_is_not_offered(santhosh):
    from meridian import services
    from meridian.db import CourtesyCredit
    rid = services.create_request("Santhosh", "Standard", "My autopay charge was $30 more than my usual rent.")
    triage_request(rid)
    with SessionLocal() as s:
        assert s.scalar(select(CourtesyCredit)) is None and s.get(Request, rid).status == "routed"


def test_general_question_answered_from_knowledge_base_without_ticket(santhosh):
    conv = new_chat(santhosh)
    out = interaction.handle_customer_message(santhosh, conv, "What time do the gates open?")
    assert out["request_id"] is None and "6:00 AM" in out["reply"]
    with SessionLocal() as s:
        assert s.scalar(select(Request)) is None


def test_question_not_in_knowledge_base_becomes_ticket(santhosh):
    conv = new_chat(santhosh)
    out = interaction.handle_customer_message(santhosh, conv, "Can I store a boat trailer in the outdoor space?")
    assert out["request_id"] is not None


def test_vague_billing_complaint_gets_a_clarifying_question(santhosh):
    conv = new_chat(santhosh)
    out = interaction.handle_customer_message(santhosh, conv, "I think my last statement is wrong but I'm not sure by how much")
    assert out["request_id"] is None and "how much" in out["reply"].lower()


def test_follow_up_attaches_to_own_open_ticket(santhosh):
    from meridian.db import Action
    conv = new_chat(santhosh)
    rid = interaction.handle_customer_message(santhosh, conv, "Water is leaking from the ceiling into my unit")["request_id"]
    out = interaction.handle_customer_message(santhosh, conv, "Also it's unit 204 on the second floor")
    assert out["request_id"] is None and out["linked_request"] == rid
    with SessionLocal() as s:
        assert s.scalar(select(Action).where(Action.request_id == rid, Action.action_type == "follow_up_note"))


def test_routed_reply_is_specific_with_next_steps(santhosh):
    conv = new_chat(santhosh)
    _, last = _ask(santhosh, conv, "My gate code stopped working this morning and I cannot get in")
    assert "Access Control" in last and "call box" in last


def test_offer_reply_guard_allows_only_offered_and_claimed_amounts():
    outcome = {"request_id": "TR-9", "final_action": "awaiting_customer_decision", "claimed_amount": 30.0,
               "credit": {"amount": 20.0, "status": "offered"}}
    assert interaction.guard_outcome_reply("We can verify $20.00 rather than $30.00. Apply it? TR-9", outcome)
    assert interaction.guard_outcome_reply("We can give you $25.00. TR-9", outcome) is None
    assert interaction.guard_outcome_reply("We will refund you $20.00. TR-9", outcome) is None


# ---------------------------------------------------------------- one ticket per conversation + context
def test_one_ticket_per_conversation(santhosh):
    from meridian.db import Action
    conv = new_chat(santhosh)
    rid = interaction.handle_customer_message(santhosh, conv, "My gate code stopped working this morning")["request_id"]
    out = interaction.handle_customer_message(santhosh, conv, "Also my unit door lock is jammed and will not open today")
    assert out["request_id"] is None and out["linked_request"] == rid
    with SessionLocal() as s:
        assert len(s.scalars(select(Request).where(Request.conversation_id == conv)).all()) == 1
        assert s.scalar(select(Action).where(Action.request_id == rid, Action.action_type == "follow_up_note"))


def test_screenshot_flow_status_yes_thanks_do_not_open_tickets(santhosh):
    conv = new_chat(santhosh)
    rid, _ = _ask(santhosh, conv, "My gate code stopped working this morning and I cannot get in")
    rid = rid["request_id"]
    status = interaction.handle_customer_message(santhosh, conv, "ok what is the latest update on this ticket")
    assert status["request_id"] is None and rid in status["reply"] and "with our" in status["reply"]
    yes = interaction.handle_customer_message(santhosh, conv, "yes")["reply"]
    assert "tell me a bit more" not in yes.lower()
    bye = interaction.handle_customer_message(santhosh, conv, "thank you")["reply"]
    assert "welcome" in bye.lower() and rid in bye
    with SessionLocal() as s:
        assert len(s.scalars(select(Request).where(Request.conversation_id == conv)).all()) == 1


def test_s_means_yes_to_an_open_offer(santhosh):
    from meridian.db import CourtesyCredit
    conv = new_chat(santhosh)
    _ask(santhosh, conv, "My autopay charge this month was $30 more than my usual rent, please fix it")
    interaction.handle_customer_message(santhosh, conv, "s")
    with SessionLocal() as s:
        assert s.scalar(select(CourtesyCredit)).status == "issued"


def test_status_question_never_accepts_an_offer(santhosh):
    from meridian.db import CourtesyCredit
    conv = new_chat(santhosh)
    _ask(santhosh, conv, "I was overcharged $40 on my storage bill this cycle, please refund the difference")
    reply = interaction.handle_customer_message(santhosh, conv, "ok what is the latest update on this ticket")["reply"]
    with SessionLocal() as s:
        assert s.scalar(select(CourtesyCredit)).status == "offered"      # still waiting for an explicit yes
    assert "waiting for your reply" in reply


def test_clear_yes_detection():
    assert interaction._is_clear_yes("yes") and interaction._is_clear_yes("S") and interaction._is_clear_yes("ok please apply it")
    assert not interaction._is_clear_yes("ok what is the latest update on this ticket")
    assert not interaction._is_clear_yes("yes but why only $20?") and not interaction._is_clear_yes("no")


def test_asking_about_own_ticket_by_number_in_new_conversation_looks_it_up(santhosh):
    first = new_chat(santhosh)
    rid, _ = _ask(santhosh, first, "My gate code stopped working this morning and I cannot get in")
    rid = rid["request_id"]
    conv = new_chat(santhosh)                                   # fresh conversation, like the screenshot
    out = interaction.handle_customer_message(santhosh, conv, f"i want to know about my ticket {rid}")
    assert out["request_id"] is None and rid in out["reply"] and "with our" in out["reply"]
    with SessionLocal() as s:
        assert s.scalar(select(Request).where(Request.conversation_id == conv)) is None


def test_other_customers_ticket_number_reveals_nothing(santhosh):
    other = make_customer("omar", "Omar Reyes")
    theirs = interaction.handle_customer_message(other, new_chat(other), "Water is leaking into my unit right now")["request_id"]
    out = interaction.handle_customer_message(santhosh, new_chat(santhosh), f"what is the status of {theirs}?")
    assert out["request_id"] is None and "couldn't find" in out["reply"] and "Water" not in out["reply"]


def test_submit_is_blocked_when_customer_references_own_ticket(santhosh):
    rid = interaction.handle_customer_message(santhosh, new_chat(santhosh), "My unit door lock is jammed today")["request_id"]
    conv = new_chat(santhosh)

    class EagerLLM:                        # an LLM that wrongly tries to open a new ticket
        model = "eager"
        def complete(self, system, messages, tools):
            from meridian.llm.base import LLMResponse
            if len(messages) == 1:
                tu = {"id": "t1", "name": "submit_service_request", "input": {"summary": "x", "language": "en"}}
                return LLMResponse(raw_content=[{"type": "tool_use", **tu}], tool_uses=[tu], text="", stop_reason="tool_use")
            return LLMResponse(raw_content=[{"type": "text", "text": "ok"}], tool_uses=[], text="ok", stop_reason="end_turn")
    out = interaction.handle_customer_message(santhosh, conv, f"any news on {rid}? it is still jammed", llm=EagerLLM())
    assert out["request_id"] is None


@pytest.mark.parametrize("question", ["What aare all the tickets i have not resolved yet", "waht are all the ticket i have",
                                      "which of my requests are still open?"])
def test_listing_my_tickets_never_opens_a_ticket(santhosh, question):
    first = interaction.handle_customer_message(santhosh, new_chat(santhosh), "My unit door lock is jammed today")["request_id"]
    conv = new_chat(santhosh)
    out = interaction.handle_customer_message(santhosh, conv, question)
    assert out["request_id"] is None and first in out["reply"]
    with SessionLocal() as s:
        assert s.scalar(select(Request).where(Request.conversation_id == conv)) is None


def test_ticket_list_detection_does_not_block_real_new_issues():
    assert chat.asks_for_ticket_list("waht are all the ticket i have")
    assert not chat.asks_for_ticket_list("I want to raise a complaint about my bill")
    assert not chat.asks_for_ticket_list("any update on TR-6069")


def test_rate_limit_refusal_is_explained(santhosh, monkeypatch):
    from meridian import config
    monkeypatch.setenv("MERIDIAN_CHAT_MAX_REQUESTS_PER_HOUR", "0")
    config.get_settings.cache_clear()
    out = interaction.handle_customer_message(santhosh, new_chat(santhosh), "My unit door lock is jammed and will not open")
    config.get_settings.cache_clear()
    assert out["request_id"] is None and "last hour" in out["reply"]


def test_deleted_conversation_is_hidden_but_its_ticket_lives_on(billing):
    u = make_customer("tessa", "Tessa Alder")
    other = make_customer("bob", "Bob Ray")
    conv = new_chat(u)
    rid = interaction.handle_customer_message(u, conv, "My autopay was $22.40 more than my usual rent.")["request_id"]
    with pytest.raises(chat.NotYourConversation):
        chat.delete_conversation(other["id"], conv)                       # only the owner can delete
    chat.delete_conversation(u["id"], conv)
    assert conv not in [c["id"] for c in chat.list_conversations(u["id"])]
    with pytest.raises(chat.NotYourConversation):
        chat.add_message(u["id"], conv, "customer", "hello?")            # no writing into a deleted chat
    worker.run_once()                                                     # the ticket is still worked on,
    with SessionLocal() as s:
        assert s.get(Request, rid).customer_notified_at is not None       # its reply still lands (for audit)
    assert rid in [r["request_id"] for r in chat.customer_requests(u["id"])]
