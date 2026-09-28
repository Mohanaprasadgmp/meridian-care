"""Customer portal: chat with the Customer Interaction Agent. Rendered by the router for role=customer."""
import streamlit as st

from meridian import chat
from meridian.agent import interaction
from ui import ORANGE, STATUS, chat_css, esc, section

user = st.session_state.get("user")
if not user or user.get("role") != "customer":          # role enforced on every rerun
    st.error("Customers only.")
    st.stop()
chat_css()

TICKET_LABEL = {"new": "Received", "processing": "Being reviewed", "routed": "With our team",
                "resolved": "Resolved", "closed_duplicate": "Merged with earlier ticket",
                "needs_review": "With a specialist", "acknowledged": "Acknowledged",
                "awaiting_customer": "Waiting for your reply"}
first_name = (user["display_name"] or user["username"]).split()[0]


def _open_new_conversation() -> None:
    st.session_state["conv_id"] = chat.start_conversation(user["id"], greeting=interaction.greeting(user["display_name"]))


@st.dialog("Delete conversation?")
def confirm_delete(conv_id: str, label: str, tickets: int) -> None:
    st.write(f"**{label}** will be removed from your list.")
    if tickets:
        st.caption("Its tickets are not cancelled: our team keeps working on them, and you can still ask about "
                   "them in any conversation.")
    yes, no = st.columns(2)
    if yes.button("Delete", type="primary", icon=":material/delete:", width="stretch"):
        chat.delete_conversation(user["id"], conv_id)
        if st.session_state.get("conv_id") == conv_id:
            st.session_state.pop("conv_id")
        st.rerun()
    if no.button("Cancel", width="stretch"):
        st.rerun()


# ---------------------------------------------------------------- sidebar
conversations = chat.list_conversations(user["id"])
if st.session_state.get("conv_id") not in {c["id"] for c in conversations}:   # first visit, or it was deleted
    st.session_state.pop("conv_id", None)
if "conv_id" not in st.session_state:          # every sign-in starts on a fresh conversation
    latest = conversations[0]["id"] if conversations else None
    if latest and not any(m["sender"] == "customer" for m in chat.get_messages(user["id"], latest)):
        st.session_state["conv_id"] = latest   # reuse an untouched one instead of piling up empty chats
    else:
        _open_new_conversation()
        conversations = chat.list_conversations(user["id"])

with st.sidebar:
    st.html('<div class="mc-eyebrow">Meridian Self Storage</div>'
            '<div style="font-size:1.35rem;font-weight:800;margin:2px 0 10px">Customer Care</div>')
    if st.button("New conversation", icon=":material/add_comment:", width="stretch", type="primary"):
        _open_new_conversation()
        st.rerun()
    st.divider()
    section("Your conversations")
    for c in conversations:
        label = f"{c['created_at']:%d %b, %H:%M} · {c['tickets']} ticket{'s' if c['tickets'] != 1 else ''}"
        active = c["id"] == st.session_state["conv_id"]
        pick, drop = st.columns([5, 1], vertical_alignment="center", gap="small")
        if pick.button(label, key=f"conv_{c['id']}", width="stretch", type="primary" if active else "secondary",
                       icon=":material/chat_bubble:"):
            st.session_state["conv_id"] = c["id"]
            st.rerun()
        if drop.button("", key=f"del_{c['id']}", icon=":material/delete:", type="tertiary",
                       help="Delete this conversation"):
            confirm_delete(c["id"], label, c["tickets"])
    st.divider()
    section("Signed in")
    st.html(f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">'
            f'<div class="mc-dot" style="background:{ORANGE}">{esc(first_name[:1].upper())}</div>'
            f'<div><b>{esc(user["display_name"])}</b><div class="mc-meta">{esc(user.get("account_tier") or "")} account'
            f'</div></div></div>')
    if st.button("Log out", icon=":material/logout:", width="stretch"):
        for k in ("user", "conv_id"):
            st.session_state.pop(k, None)
        st.rerun()

# ---------------------------------------------------------------- chat
st.html(f'<div class="mc-hero"><div><div class="mc-eyebrow">Customer care</div>'
        f'<h1>Hi {esc(first_name)}, how can we help?</h1>'
        f'<p>Describe your issue. Our assistant logs it and the care team replies right here.</p></div></div>')


@st.fragment(run_every=3)
def conversation_view() -> None:
    """Re-renders every 3 s so the Triage Agent's answer appears without the customer refreshing."""
    try:
        msgs = chat.get_messages(user["id"], st.session_state["conv_id"])   # ownership checked here
    except chat.NotYourConversation:
        st.session_state.pop("conv_id", None)
        st.error("That conversation isn't available.")
        return
    # Customer on the right, assistant on the left. Each ticket's status badge shows once, on its latest message.
    last_for_ticket = {m["request_id"]: m["id"] for m in msgs if m["request_id"] and m["sender"] == "assistant"}
    rows = []
    for m in msgs:
        mine = m["sender"] == "customer"
        body = esc(m["content"]).replace("\n", "<br>")
        badge = ""
        if not mine and m["request_id"] and last_for_ticket.get(m["request_id"]) == m["id"]:
            status = m["request_status"] or "new"
            badge = (f'<div class="mc-ticket" style="background:{STATUS.get(status, "#7F8C8D")}">'
                     f'{esc(m["request_id"])} · {esc(TICKET_LABEL.get(status, status))}</div>')
        who = "You" if mine else "Meridian assistant"
        rows.append(f'<div class="mc-row {"me" if mine else "bot"}">'
                    + ("" if mine else '<div class="mc-avatar">M</div>')
                    + f'<div class="mc-bubble"><div class="mc-who">{who} · {m["created_at"]:%H:%M}</div>'
                    f'{body}{badge}</div></div>')
    st.html('<div class="mc-thread">' + "".join(rows) + "</div>")
    waiting = sorted({m["request_id"] for m in msgs if m["request_id"] and m["request_status"] in ("new", "processing")})
    answered = {m["request_id"] for m in msgs if m["request_id"] and m["request_status"] not in ("new", "processing")}
    pending = [r for r in waiting if r not in answered]
    if pending:
        st.html(f'<div class="mc-typing">Our care team is reviewing {esc(", ".join(pending))}. '
                'The answer will appear here automatically.</div>')


conversation_view()

prompt = st.chat_input("Describe your issue…", max_chars=2000)
if prompt:
    with st.spinner("Assistant is typing…"):
        try:
            interaction.handle_customer_message(user, st.session_state["conv_id"], prompt)
        except ValueError as e:
            st.error(str(e))
        except chat.NotYourConversation:
            st.session_state.pop("conv_id", None)
    st.rerun()
