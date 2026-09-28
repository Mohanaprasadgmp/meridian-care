# Original path: src/meridian/auth.py
"""Console authentication: PBKDF2-SHA256 password hashes (stdlib only) with brute-force lockout."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from .db import SessionLocal, User, utcnow

ITERATIONS = 600_000          # OWASP 2023 guidance for PBKDF2-SHA256
MIN_PASSWORD_LEN = 8
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=5)
_DUMMY_HASH = None            # verified against when the username is unknown, so timing doesn't reveal it


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def normalize(username: str) -> str:
    return username.strip().lower()


ROLES = ("admin", "customer")
TIERS = ("Standard", "Business Elite")


def upsert_user(username: str, password: str, role: str = "admin", display_name: Optional[str] = None,
                account_tier: Optional[str] = None) -> bool:
    """Create the user, or reset their password (and profile). Returns True if created.

    Customers must have a display_name: it is the tenant name used on their requests and for billing
    matching, so it is set by an administrator and never taken from anything the customer types."""
    name = normalize(username)
    if not name or len(name) > 64:
        raise ValueError("username must be 1-64 characters")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    display_name = " ".join((display_name or "").split()) or None
    if role == "customer":
        if not display_name:
            raise ValueError("customer accounts need a display name (the tenant name)")
        account_tier = account_tier or "Standard"
        if account_tier not in TIERS:
            raise ValueError(f"account tier must be one of {TIERS}")
    with SessionLocal() as s:
        user = s.scalar(select(User).where(User.username == name))
        created = user is None
        if created:
            user = User(username=name, password_hash=hash_password(password))
            s.add(user)
        else:
            user.password_hash, user.failed_attempts, user.locked_until = hash_password(password), 0, None
        user.role, user.display_name, user.account_tier = role, display_name, account_tier
        s.commit()
    return created


def list_users(role: Optional[str] = None) -> list[dict]:
    with SessionLocal() as s:
        q = select(User).order_by(User.username)
        if role:
            q = q.where(User.role == role)
        return [{"id": u.id, "username": u.username, "role": u.role, "display_name": u.display_name,
                 "account_tier": u.account_tier, "last_login_at": u.last_login_at} for u in s.scalars(q)]


def user_count() -> int:
    with SessionLocal() as s:
        return len(s.scalars(select(User.id)).all())


def authenticate(username: str, password: str, now: Optional[datetime] = None) -> tuple[bool, str]:
    """Returns (ok, message). Messages never reveal whether the username exists."""
    global _DUMMY_HASH
    now = now or utcnow()
    generic = "Invalid username or password."
    with SessionLocal() as s:
        user = s.scalar(select(User).where(User.username == normalize(username)))
        if user is None:
            _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
            verify_password(password, _DUMMY_HASH)
            return False, generic
        locked = user.locked_until and user.locked_until.replace(tzinfo=user.locked_until.tzinfo or timezone.utc) > now
        if locked:
            return False, "Too many failed attempts. Try again in a few minutes."
        if not verify_password(password, user.password_hash):
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
                user.locked_until, user.failed_attempts = now + LOCKOUT, 0
            s.commit()
            return False, generic
        user.failed_attempts, user.locked_until, user.last_login_at = 0, None, now
        s.commit()
        return True, user.username


def login(username: str, password: str, role: str) -> tuple[bool, str, Optional[dict]]:
    """Authenticate AND check the account belongs to this portal. Returns (ok, message, user_info)."""
    ok, msg = authenticate(username, password)
    if not ok:
        return False, msg, None
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.username == msg))
        if (u.role or "admin") != role:
            return False, "Invalid username or password.", None     # same message: don't reveal the account exists
        return True, u.username, {"id": u.id, "username": u.username, "role": u.role,
                                  "display_name": u.display_name or u.username, "account_tier": u.account_tier}
