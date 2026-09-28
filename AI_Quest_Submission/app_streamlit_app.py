# Original path: app/streamlit_app.py
"""Meridian Care - entry point. Run: streamlit run app/streamlit_app.py

Landing page with two portals (Customer / Administrator). After sign-in the user is routed by role:
customers get the chat with the Customer Interaction Agent, administrators get the triage console.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st  # noqa: E402

from meridian import auth  # noqa: E402
from meridian.config import get_settings  # noqa: E402
from meridian.db import init_db  # noqa: E402
from ui import esc, inject_css, landing_css  # noqa: E402

st.set_page_config(page_title="Meridian Care", page_icon=":material/support_agent:", layout="wide")
init_db()
inject_css()


@st.cache_resource(show_spinner=False)
def _background_worker():
    """One triage worker thread per server process (claims are atomic, so replicas can each run one)."""
    from meridian.agent.worker import Worker
    return Worker().start()


if get_settings().embedded_worker:
    _background_worker()

user = st.session_state.get("user")
if not isinstance(user, dict):          # not signed in (or a session from an older version)
    st.session_state.pop("user", None)
    landing_css()
    st.html('<div class="mc-land"><div class="mc-eyebrow">Meridian Self Storage</div>'
            '<h1>Customer Care</h1><p>Help with your unit, billing and access, backed by an AI assistant '
            'and our care team.</p></div>')
    if auth.user_count() == 0:
        st.info("No accounts exist yet. Create one in a terminal, then refresh:  \n"
                "`python -m meridian create-user admin --role admin`", icon=":material/person_add:")
    portals = {"customer": ("Customer", "Chat with our assistant about billing, gate access, your unit or damage.",
                            ":material/chat:"),
               "admin": ("Administrator", "Review the AI triage queue, reasoning, credits and audit trail.",
                         ":material/admin_panel_settings:")}
    role = st.session_state.get("portal")
    if role not in portals:                      # step 1: choose who you are
        _, c1, _, c2, _ = st.columns([0.8, 2, 0.25, 2, 0.8])
        for col, (key, (title, blurb, icon)) in zip((c1, c2), portals.items()):
            with col, st.container(key=f"portal_{key}"):
                st.html(f'<div class="mc-portal"><h3>{esc(title)}</h3><p>{esc(blurb)}</p></div>')
                if st.button(f"{title} login", key=f"pick_{key}", icon=icon, type="primary", width="stretch"):
                    st.session_state["portal"] = key
                    st.rerun()
        st.stop()
    title, _, icon = portals[role]               # step 2: the login card for that portal slides in
    _, mid, _ = st.columns([1.3, 2, 1.3])
    with mid, st.container(key="login_card"):
        st.html(f'<div class="mc-portal"><h3>{esc(title)} login</h3><p>Sign in with the account our team '
                f'gave you.</p></div>')
        with st.form(f"login_{role}"):
            username = st.text_input("Username", key=f"u_{role}", icon=":material/person:", autocomplete="username")
            password = st.text_input("Password", key=f"p_{role}", type="password", icon=":material/lock:",
                                     autocomplete="current-password")
            if st.form_submit_button("Sign in", type="primary", width="stretch", icon=icon):
                ok, msg, info = auth.login(username, password, role)
                if ok:
                    st.session_state["user"] = info
                    st.session_state.pop("portal", None)
                    st.rerun()
                st.error(msg, icon=":material/error:")
        if st.button("Back", icon=":material/arrow_back:", type="tertiary"):
            st.session_state.pop("portal", None)
            st.rerun()
    st.stop()

page = "admin_console.py" if user["role"] == "admin" else "customer_chat.py"
st.navigation([st.Page(page, title="Meridian Care")], position="hidden").run()
