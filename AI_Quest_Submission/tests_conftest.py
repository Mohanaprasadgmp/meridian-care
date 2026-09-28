# Original path: tests/conftest.py
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from meridian import config, db  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MERIDIAN_DATABASE_URL", f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setenv("MERIDIAN_LLM_PROVIDER", "fake")
    # Frozen copy of the original seed data: tests must not depend on edits to data/
    monkeypatch.setenv("MERIDIAN_DATA_DIR", str(ROOT / "tests" / "fixtures"))
    config.get_settings.cache_clear()
    db.reset_engine()
    db.init_db()
    yield
    db.reset_engine()
    config.get_settings.cache_clear()


@pytest.fixture()
def seeded(fresh_db):
    from meridian.ingest import ingest
    s = config.get_settings()
    ingest(s.data_dir / "storage_requests.csv", s.data_dir / "storage_billing_records.csv")
