# Original path: tests/test_auth.py
"""Console login: hashing, authentication, lockout, and survival across data resets."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from meridian import auth
from meridian.db import SessionLocal, User, init_db, utcnow


def test_hash_is_salted_and_verifies():
    h1, h2 = auth.hash_password("correct horse"), auth.hash_password("correct horse")
    assert h1 != h2 and h1.startswith("pbkdf2_sha256$")
    assert auth.verify_password("correct horse", h1) and not auth.verify_password("wrong", h1)
    assert not auth.verify_password("x", "garbage")


def test_password_never_stored_in_plain_text(fresh_db):
    auth.upsert_user("Ana", "s3cret-pass")
    with SessionLocal() as s:
        u = s.scalar(select(User))
    assert u.username == "ana" and "s3cret-pass" not in u.password_hash


def test_authenticate_success_and_generic_failures(fresh_db):
    auth.upsert_user("ana", "s3cret-pass")
    assert auth.authenticate(" ANA ", "s3cret-pass") == (True, "ana")      # username is case/space-insensitive
    ok1, msg1 = auth.authenticate("ana", "wrong-pass")
    ok2, msg2 = auth.authenticate("nobody", "whatever1")
    assert not ok1 and not ok2 and msg1 == msg2                           # doesn't reveal which usernames exist


def test_lockout_after_repeated_failures(fresh_db):
    auth.upsert_user("ana", "s3cret-pass")
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth.authenticate("ana", "wrong-pass")
    ok, msg = auth.authenticate("ana", "s3cret-pass")
    assert not ok and "Too many" in msg                                   # even the right password is refused
    later = utcnow() + auth.LOCKOUT + timedelta(seconds=1)
    assert auth.authenticate("ana", "s3cret-pass", now=later)[0]


def test_upsert_resets_password_and_validates(fresh_db):
    assert auth.upsert_user("ana", "first-pass") is True
    assert auth.upsert_user("ana", "second-pass") is False
    assert auth.authenticate("ana", "second-pass")[0] and not auth.authenticate("ana", "first-pass")[0]
    with pytest.raises(ValueError):
        auth.upsert_user("ana", "short")
    with pytest.raises(ValueError):
        auth.upsert_user("   ", "long-enough")


def test_data_reset_keeps_accounts(fresh_db):
    auth.upsert_user("ana", "s3cret-pass")
    init_db(reset=True)
    assert auth.user_count() == 1 and auth.authenticate("ana", "s3cret-pass")[0]
