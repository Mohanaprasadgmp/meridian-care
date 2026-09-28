# Original path: src/meridian/ingest.py
"""CSV -> DB ingestion. Idempotent: re-running never duplicates or clobbers triage state."""
import csv
from datetime import datetime
from pathlib import Path

from .db import BillingRecord, Request, SessionLocal
from .observability import log

REQUIRED_REQUEST_COLS = {"request_id", "customer_name", "account_tier", "status", "submitted_at", "body"}
REQUIRED_BILLING_COLS = {"customer_name", "verified_discrepancy"}


def _read(path: Path, required: set[str]) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name} missing columns: {sorted(missing)}")
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def ingest(requests_csv: Path, billing_csv: Path) -> dict:
    stats = {"requests_new": 0, "requests_skipped": 0, "billing_upserted": 0, "rejected": 0}
    with SessionLocal() as s:
        for row in _read(requests_csv, REQUIRED_REQUEST_COLS):
            if not row["request_id"] or not row["body"]:
                stats["rejected"] += 1
                log.warning("ingest.rejected_row", extra={"row_id": row.get("request_id")})
                continue
            if s.get(Request, row["request_id"]):
                stats["requests_skipped"] += 1
                continue
            s.add(Request(
                request_id=row["request_id"],
                customer_name=row["customer_name"],
                account_tier=row["account_tier"] or "Standard",
                source_status=row["status"],
                status=row["status"] or "new",
                submitted_at=datetime.fromisoformat(row["submitted_at"].replace("Z", "+00:00")),
                body=row["body"][:5000],  # bound input size
                source="csv",
            ))
            stats["requests_new"] += 1
        for row in _read(billing_csv, REQUIRED_BILLING_COLS):
            s.merge(BillingRecord(customer_name=row["customer_name"],
                                  verified_discrepancy=round(float(row["verified_discrepancy"]), 2)))
            stats["billing_upserted"] += 1
        s.commit()
    log.info("ingest.done", extra=stats)
    return stats
