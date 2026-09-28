"""Shared design system for all Streamlit pages: tokens, CSS and small HTML helpers."""
import html

import streamlit as st

# ---------------------------------------------------------------- design tokens
INK, MUTED, ORANGE, LINE = "#1F2A36", "#5B6B7A", "#F26B21", "#E1E6EB"
PRIO = {"P1": ("#C0392B", "Critical"), "P2": ("#E67E22", "High"), "P3": ("#C9A227", "Normal"), "P4": ("#7F8C8D", "Low")}
STATUS = {"routed": "#2E6DA4", "resolved": "#2E8B57", "closed_duplicate": "#7F8C8D", "needs_review": "#C0392B",
          "acknowledged": "#2E6DA4", "new": "#8E44AD", "open": "#8E44AD", "closed": "#7F8C8D"}
CREDIT = {"issued": "#2E8B57", "pending_approval": "#E67E22", "reversed": "#7F8C8D", "rejected": "#C0392B"}
esc = lambda v: html.escape("" if v is None else str(v))  # noqa: E731  - all tenant/LLM text is escaped before HTML


def inject_css() -> None:
    st.html("""
<style>
  #MainMenu, footer, [data-testid="stDecoration"] {visibility: hidden;}
  .block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px;}
  .mc-hero {background: #1F2A36; border-radius: 14px; padding: 22px 28px; color: #fff; margin-bottom: 14px;
            display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap;}
  .mc-hero h1 {font-size: 1.65rem; margin: 0; color: #fff; font-weight: 700; letter-spacing: -0.01em;}
  .mc-hero p {margin: 4px 0 0; color: #CBD5DF; font-size: 0.95rem;}
  .mc-eyebrow {color: #F26B21; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase;}
  .mc-chip {display: inline-block; background: #2C3A4A; color: #E6ECF1; border-radius: 999px; padding: 5px 12px;
            font-size: 0.78rem; margin: 3px 0 3px 6px; white-space: nowrap;}
  .mc-chip b {color: #F26B21; font-weight: 700;}
  .mc-kpi {background: #fff; border: 1px solid #E1E6EB; border-radius: 12px; padding: 14px 16px; height: 100%;
           box-shadow: 0 1px 3px rgba(0,0,0,0.04);}
  .mc-kpi .lbl {color: #5B6B7A; font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;}
  .mc-kpi .val {font-size: 1.9rem; font-weight: 800; line-height: 1.15; margin-top: 4px;}
  .mc-kpi .sub {color: #5B6B7A; font-size: 0.8rem; margin-top: 2px;}
  .mc-lane {border-radius: 12px; padding: 12px 16px; color: #fff;}
  .mc-lane .n {font-size: 1.8rem; font-weight: 800; line-height: 1.1;}
  .mc-lane .t {font-size: 0.8rem; opacity: 0.92; font-weight: 600;}
  .mc-pill {display: inline-block; border-radius: 999px; padding: 3px 11px; font-size: 0.78rem; font-weight: 700;
            color: #fff; margin: 2px 4px 2px 0; white-space: nowrap;}
  .mc-pill.ghost {background: #EEF1F4; color: #1F2A36; font-weight: 600;}
  .mc-card {background: #fff; border: 1px solid #E1E6EB; border-radius: 12px; padding: 16px 18px; margin-bottom: 12px;}
  .mc-card h3 {margin: 0 0 4px; font-size: 1.2rem;}
  .mc-meta {color: #5B6B7A; font-size: 0.85rem;}
  .mc-quote {border-left: 4px solid #F26B21; background: #FDF3EC; padding: 12px 14px; border-radius: 6px;
             margin-top: 10px; font-size: 1rem; color: #1F2A36;}
  .mc-sec {font-size: 0.78rem; font-weight: 700; color: #5B6B7A; text-transform: uppercase; letter-spacing: 0.08em;
           margin: 6px 0 8px;}
  .mc-kv {display: grid; grid-template-columns: 130px 1fr; gap: 6px 12px; font-size: 0.9rem;}
  .mc-kv .k {color: #5B6B7A;}
  .mc-code {font-family: ui-monospace, Consolas, monospace; background: #F4F6F8; border-radius: 6px; padding: 8px 10px;
            font-size: 0.82rem; color: #1F2A36; word-break: break-word;}
  .mc-tl {position: relative; margin-left: 6px;}
  .mc-step {display: grid; grid-template-columns: 30px 1fr; gap: 10px; padding-bottom: 12px; position: relative;}
  .mc-step:not(:last-child)::before {content: ""; position: absolute; left: 14px; top: 30px; bottom: 0; width: 2px;
                                      background: #E1E6EB;}
  .mc-dot {width: 30px; height: 30px; border-radius: 50%; color: #fff; font-weight: 800; font-size: 0.8rem;
           display: flex; align-items: center; justify-content: center;}
  .mc-step .nm {font-family: ui-monospace, Consolas, monospace; font-weight: 700; font-size: 0.9rem;}
  .mc-step .rs {color: #5B6B7A; font-size: 0.84rem; margin-top: 2px;}
  .mc-msg {background: #EEF6F1; border: 1px solid #CFE6D8; border-radius: 10px; padding: 10px 12px; font-size: 0.92rem;}
  div[data-testid="stTabs"] button p {font-size: 0.95rem; font-weight: 600;}
</style>
""")


def pill(text, color=None) -> str:
    return (f'<span class="mc-pill" style="background:{color}">{esc(text)}</span>' if color
            else f'<span class="mc-pill ghost">{esc(text)}</span>')


def kpi(col, label, value, sub="", color=INK):
    col.html(f'<div class="mc-kpi"><div class="lbl">{esc(label)}</div>'
             f'<div class="val" style="color:{color}">{esc(value)}</div><div class="sub">{esc(sub)}</div></div>')


def table_height(n_rows: int, max_px: int = 560) -> int:
    """Fit the grid to its rows (header + rows, ~35px each) so short tables don't show empty filler rows."""
    return min(max_px, 35 * (n_rows + 1) + 3)


def section(title):
    st.html(f'<div class="mc-sec">{esc(title)}</div>')




STATUS["processing"] = "#8E44AD"


def _storage_facade_svg() -> str:
    """Background art: a self-storage facility at dusk, rows of roll-up unit doors (one lit in brand orange).
    Generated inline so it works offline / behind corporate proxies and carries no image licensing."""
    doors = []
    for row, y in enumerate((330, 560)):
        for col in range(9):
            x = 90 + col * 160
            lit = (row, col) in {(0, 5), (1, 2)}
            fill = "#F26B21" if lit else "#3A4A5C"
            doors.append(f'<rect x="{x}" y="{y}" width="130" height="190" rx="4" fill="{fill}" opacity="{0.9 if lit else 0.55}"/>')
            doors += [f'<rect x="{x}" y="{y + 14 + k * 18}" width="130" height="3" fill="#1F2A36" opacity="0.35"/>'
                      for k in range(10)]
            doors.append(f'<text x="{x + 65}" y="{y - 10}" font-family="Arial" font-size="18" fill="#8FA1B3" '
                         f'text-anchor="middle">{row + 1}{col + 1:02d}</text>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice">'
        '<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#141C26"/>'
        '<stop offset="1" stop-color="#2C3A4A"/></linearGradient></defs>'
        '<rect width="1600" height="900" fill="url(#sky)"/>'
        '<rect x="40" y="250" width="1520" height="560" fill="#243140"/>'
        '<rect x="40" y="235" width="1520" height="22" fill="#1A2430"/>'
        + "".join(doors) +
        '<rect x="0" y="810" width="1600" height="90" fill="#10161E"/>'
        '<rect x="0" y="0" width="1600" height="900" fill="#0E141B" opacity="0.45"/></svg>')


def landing_css() -> None:
    from urllib.parse import quote
    uri = "data:image/svg+xml," + quote(_storage_facade_svg())
    st.html(f"""<style>
      .stApp {{background: url("{uri}") center / cover no-repeat fixed;}}
      [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], header {{display: none;}}
      .mc-land {{text-align: center; color: #fff; margin: 4vh 0 28px;}}
      .mc-land h1 {{font-size: 2.6rem; font-weight: 800; margin: 6px 0; color: #fff;}}
      .mc-land p {{color: #CBD5DF; font-size: 1.1rem; margin: 0;}}
      [class*="st-key-portal_"], .st-key-login_card {{background: rgba(255,255,255,0.97); border-radius: 16px;
                   padding: 22px 24px 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.35);}}
      [class*="st-key-portal_"] {{animation: mcRise .5s ease-out both; transition: transform .2s, box-shadow .2s;}}
      .st-key-portal_admin {{animation-delay: .12s;}}
      [class*="st-key-portal_"]:hover {{transform: translateY(-4px); box-shadow: 0 16px 40px rgba(0,0,0,0.45);}}
      .st-key-login_card {{animation: mcRise .45s cubic-bezier(.2,.8,.2,1) both;}}
      .st-key-login_card div[data-testid="stForm"] {{border: none; padding: 0;}}
      @keyframes mcRise {{from {{opacity: 0; transform: translateY(28px) scale(.97);}}
                          to {{opacity: 1; transform: none;}}}}
      .mc-portal h3 {{margin: 0 0 4px; font-size: 1.3rem;}}
      .mc-portal p {{color: #5B6B7A; margin: 0 0 6px; min-height: 3em;}}
    </style>""")


def chat_css() -> None:
    st.html("""<style>
      .mc-thread {display:flex; flex-direction:column; gap:10px; padding:4px 2px 12px;}
      .mc-row {display:flex; align-items:flex-end; gap:8px;}
      .mc-row.me {justify-content:flex-end;}
      .mc-bubble {max-width:72%; padding:10px 14px; border-radius:16px; font-size:0.97rem; line-height:1.45;
                  box-shadow:0 1px 2px rgba(0,0,0,0.06); word-wrap:break-word;}
      .mc-row.me .mc-bubble {background:#F26B21; color:#fff; border-bottom-right-radius:4px;}
      .mc-row.bot .mc-bubble {background:#fff; color:#1F2A36; border:1px solid #E1E6EB; border-bottom-left-radius:4px;}
      .mc-who {font-size:0.72rem; font-weight:700; opacity:0.7; margin-bottom:3px;}
      .mc-avatar {width:30px; height:30px; flex:0 0 30px; border-radius:50%; background:#1F2A36; color:#F26B21;
                  font-weight:800; font-size:0.8rem; display:flex; align-items:center; justify-content:center;}
      .mc-ticket {display:table; margin-top:8px; font-size:0.76rem; font-weight:700; border-radius:999px;
                  padding:2px 10px; color:#fff;}
      .mc-typing {color:#5B6B7A; font-size:0.85rem; font-style:italic;}
      [class*="st-key-conv_"] button {justify-content:flex-start;}
      [class*="st-key-del_"] button {color:#7F8C8D;}
      [class*="st-key-del_"] button:hover {color:#C0392B;}
    </style>""")


STATUS["awaiting_customer"] = "#E67E22"
CREDIT.update({"offered": "#2E6DA4", "declined": "#7F8C8D"})
