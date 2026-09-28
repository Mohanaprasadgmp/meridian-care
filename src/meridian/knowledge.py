"""Customer knowledge base: markdown sections searched with keyword overlap (stdlib only, no new dependency).

The Interaction Agent may answer general questions ONLY from sections returned here; anything else becomes a
ticket. The file is business-owned content (data/knowledge_base.md) and is reloaded when it changes.
"""
import re
from functools import lru_cache
from pathlib import Path

from .config import get_settings

_STOP = set("a an the i we our my me us you your is are was were be to of for on in at it this that and or but "
            "can could would please with about as by from so if not no any some have has had do does what how when "
            "where which who there their them they its unit units storage facility meridian account".split())
MIN_SCORE = 2          # at least two shared meaningful words, or a heading-word match counted double


def _words(text: str) -> set[str]:
    words = {w for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOP and len(w) > 2}
    return words | {w[:-1] for w in words if w.endswith("s")}   # crude plural folding: fobs -> fob


def kb_path() -> Path:
    return get_settings().data_dir / "knowledge_base.md"


@lru_cache(maxsize=4)
def _sections(path: str, mtime: float) -> list[tuple[str, str]]:
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"^## +", text, flags=re.M)[1:]
    out = []
    for part in parts:
        title, _, body = part.partition("\n")
        out.append((title.strip(), " ".join(body.split())))
    return out


def sections() -> list[tuple[str, str]]:
    p = kb_path()
    return _sections(str(p), p.stat().st_mtime) if p.exists() else []


def search(query: str, limit: int = 2) -> list[dict]:
    """Return the best-matching answer sections (next-step sections are excluded)."""
    q = _words(query)
    scored = []
    for title, body in sections():
        if title.lower().startswith("next steps"):
            continue
        score = 2 * len(q & _words(title)) + len(q & _words(body))
        if score >= MIN_SCORE:
            scored.append((score, title, body))
    scored.sort(key=lambda x: -x[0])
    return [{"title": t, "content": b, "score": s} for s, t, b in scored[:limit]]


def next_steps(category: str | None) -> str | None:
    for title, body in sections():
        if category and title.lower() == f"next steps: {category}".lower():
            return body
    return None
