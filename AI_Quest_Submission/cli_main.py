# Original path: src/meridian/__main__.py
"""CLI: python -m meridian {init|ingest|run|eval|stats}"""
import argparse
import json
import sys
from pathlib import Path

from .config import get_settings


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="meridian")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="create tables (use --reset to wipe)")
    i.add_argument("--reset", action="store_true")
    sub.add_parser("ingest", help="load seed CSVs")
    r = sub.add_parser("run", help="triage pending requests")
    r.add_argument("--limit", type=int)
    r.add_argument("--workers", type=int)
    r.add_argument("--id", help="triage a single request id")
    r.add_argument("--retry-failed", action="store_true", help="re-queue requests that fell back due to provider errors")
    e = sub.add_parser("eval", help="score current DB state against gold labels")
    e.add_argument("--out", type=Path)
    sub.add_parser("stats", help="print summary")
    sub.add_parser("check-llm", help="verify connectivity, auth and tool calling for the configured LLM")
    u = sub.add_parser("create-user", help="create a login, or reset an existing user's password")
    u.add_argument("username")
    u.add_argument("--role", choices=["admin", "customer"], default="admin")
    u.add_argument("--display-name", help="customers: tenant name exactly as on the billing record")
    u.add_argument("--tier", choices=["Standard", "Business Elite"], default="Standard")
    w = sub.add_parser("worker", help="run the background triage worker (claims new chat requests, replies in chat)")
    w.add_argument("--once", action="store_true", help="process one batch and exit")
    sub.add_parser("seed-demo-customers", help="create demo customer logins named after billing-record tenants")
    a = p.parse_args(argv)
    s = get_settings()

    from .db import init_db
    if a.cmd == "init":
        init_db(reset=a.reset)
        print("db ready:", s.database_url)
    elif a.cmd == "ingest":
        init_db()
        from .ingest import ingest
        print(json.dumps(ingest(s.data_dir / "storage_requests.csv", s.data_dir / "storage_billing_records.csv")))
    elif a.cmd == "run":
        from .agent.orchestrator import requeue_failed, triage_all, triage_request
        if a.retry_failed:
            print(f"re-queued {len(requeue_failed())} requests that failed on provider errors")
        res = [triage_request(a.id)] if a.id else triage_all(limit=a.limit, workers=a.workers)
        cost = sum(x["cost_usd"] for x in res)
        fb = sum(x["status"] == "fallback" for x in res)
        print(f"triaged {len(res)} requests | fallbacks {fb} | est. cost ${cost:.2f} | agent {s.llm_provider}/{s.active_model}")
    elif a.cmd == "eval":
        from .evaluation import evaluate, print_report
        rep = evaluate()
        print_report(rep)
        if a.out:
            a.out.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    elif a.cmd == "worker":
        from .agent.worker import Worker, run_once
        init_db()
        if a.once:
            print(json.dumps(run_once(s)))
        else:
            print(f"worker running (sources={s.worker_sources}, poll={s.worker_poll_s}s) - Ctrl+C to stop")
            wk = Worker(s).start()
            try:
                wk.thread.join()
            except KeyboardInterrupt:
                wk.stop()
    elif a.cmd == "seed-demo-customers":
        return seed_demo_customers()
    elif a.cmd == "check-llm":
        return check_llm(s)
    elif a.cmd == "create-user":
        import getpass
        from .auth import upsert_user
        init_db()
        pw = getpass.getpass("Password (min 8 chars, hidden): ")
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            return 1
        try:
            created = upsert_user(a.username, pw, role=a.role, display_name=a.display_name,
                                  account_tier=a.tier if a.role == "customer" else None)
        except ValueError as e:
            print(f"Error: {e}")
            return 1
        print(f"User '{a.username.strip().lower()}' {'created' if created else 'password updated'}.")
    elif a.cmd == "stats":
        from .evaluation import summary
        print(json.dumps(summary(), indent=2))
    return 0


DEMO_CUSTOMERS = [  # tenants that exist in the billing records, so the courtesy-credit path can be demoed
    ("desmond", "Desmond Okafor", "Standard"), ("naomi", "Naomi Fitzwilliam", "Standard"),
    ("perpetua", "Perpetua Lindqvist", "Standard"), ("corinne", "Corinne Vantassel", "Business Elite"),
]


def seed_demo_customers() -> int:
    """Passwords are random and printed once; only their hashes are stored."""
    import secrets
    import string
    from .auth import upsert_user
    from .db import init_db
    init_db()
    alphabet = string.ascii_letters + string.digits
    print("username    password              tenant name")
    for username, name, tier in DEMO_CUSTOMERS:
        pw = "Tenant-" + "".join(secrets.choice(alphabet) for _ in range(8))
        upsert_user(username, pw, role="customer", display_name=name, account_tier=tier)
        print(f"{username:<11} {pw:<21} {name} ({tier})")
    print("Save these now: they are not stored in readable form.")
    return 0


def check_llm(s) -> int:
    """One tiny tool-calling request: proves endpoint, key, model name and tool support before a full run."""
    import time
    from .llm import get_llm
    print(f"provider={s.llm_provider} model={s.active_model}"
          + (f" base_url={s.company_base_url} context={s.company_context_tokens}" if s.llm_provider == "company" else ""))
    ping = [{"name": "ping", "description": "Call this to confirm tool calling works.", "strict": True,
             "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                              "additionalProperties": False}}]
    try:
        llm = get_llm(s.llm_provider)
        t0 = time.perf_counter()
        r = llm.complete("You are a connectivity check. Call the ping tool with ok=true.",
                         [{"role": "user", "content": "Call the ping tool now."}], ping)
    except Exception as e:  # show the real reason: DNS, TLS, 401, 404 model, 400 unsupported param...
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    ms = int((time.perf_counter() - t0) * 1000)
    if r.tool_uses and r.tool_uses[0]["name"] == "ping":
        print(f"OK: connected, authenticated, tool calling works ({ms} ms, model reported '{r.model}')")
        return 0
    print(f"CONNECTED but the model did not call the tool (stop_reason={r.stop_reason}). Reply: {r.text[:200]!r}\n"
          "Tool calling may be disabled for this model on the gateway: try another model, e.g. mistral-small-2506-ITG.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
