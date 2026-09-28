# Meridian Care — AI Triage Agent

Two agents: a **Customer Interaction Agent** that chats with logged-in tenants and turns their messages into requests, and a **Triage Agent** that classifies, prioritises, routes and resolves them. The answer is posted back into the customer's own chat. A landing page offers customer and administrator portals. See [docs/ARCHITECTURE.md §2b](docs/ARCHITECTURE.md).

A tool-using AI agent for Meridian Self Storage customer care. For every tenant request it:
- classifies it, checks history, and pulls extra context when unsure
- prioritises and routes it
- acknowledges it, closes it as a duplicate, or routes it to a team
- issues a courtesy credit for verified billing discrepancies

Humans review everything in a Streamlit console and can override any decision. All state is persisted to a database.

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · Demo: [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md) · Progress: [PROGRESS.md](PROGRESS.md)

## Quick start (Windows, PowerShell)
```powershell
copy .env.example .env   # set ARTIFACTORY_PYPI_URL here first if packages must come from your Artifactory
.\run.ps1 setup          # creates .venv and installs requirements.txt (from Artifactory when configured)
# then in .env: add MERIDIAN_COMPANY_API_KEY (company LLM); or OpenAI/Claude keys; fake = offline
python -m meridian check-llm   # verify endpoint, key, model and tool calling before a full run
.\run.ps1 reset          # create DB + ingest the seed CSVs
.\run.ps1 triage         # run the agent over all pending requests
.\run.ps1 eval           # score against data/gold_labels.csv -> eval/latest_eval.json
python -m meridian create-user admin --role admin   # administrator login (password prompted, stored hashed)
python -m meridian seed-demo-customers              # demo customer logins (passwords printed once)
.\run.ps1 app            # review console at http://localhost:8501 (sign in with that account)
.\run.ps1 test           # 75 tests: guardrails, protocol, failures, overrides, chat, 2-agent hand-off
```
The CLI can also be called directly: `python -m meridian {init|ingest|run|eval|stats}` (set `PYTHONPATH=src`). `run --id TR-6057` triages a single request.

## Layout
| Path | Purpose |
|---|---|
| `src/meridian/agent/orchestrator.py` | Bounded agent loop, safe fallback, parallel workers |
| `src/meridian/tools/` | Tool schemas (`definitions.py`) and guarded execution (`environment.py`) |
| `src/meridian/policy/` | Deterministic priority, routing and credit guardrail |
| `src/meridian/llm/` | Model-agnostic interface: company LLM gateway (OpenAI-compatible), OpenAI, Claude, offline keyword agent |
| `src/meridian/prompts/triage_v1.md` | Versioned system prompt |
| `src/meridian/db.py` | SQLAlchemy models (SQLite by default; Postgres via `MERIDIAN_DATABASE_URL`) |
| `src/meridian/services.py` | Human override operations, all audited |
| `src/meridian/evaluation.py` | Eval harness against the gold labels |
| `app/streamlit_app.py` | Review console |
| `data/gold_labels.csv` | Hand-labelled expected outcomes for all 100 requests |

## Installing dependencies from Artifactory
If your organisation blocks public PyPI, set these in `.env` (read by `run.ps1`; real environment variables take precedence):

| Variable | Purpose |
|---|---|
| `ARTIFACTORY_PYPI_URL` | Your PyPI repo, e.g. `https://<host>/artifactory/api/pypi/<repo>/simple`. If it needs login: `https://<user>:<token>@<host>/artifactory/api/pypi/<repo>/simple` |
| `ARTIFACTORY_NPM_URL` | Your npm repo; only needed to rebuild the slides (`.\run.ps1 deck-setup`) |
| `ARTIFACTORY_CA_BUNDLE` | Corporate root CA `.pem` if pip reports SSL certificate errors |
| `PYTHON_EXE` | Python 3.10+ used to create `.venv` if `python` is not on PATH |

Leave them blank to use public PyPI / npm. Without the script: `pip install --index-url <your-url> -r requirements.txt`.

## Docker
```
docker build --build-arg PIP_INDEX_URL=<your Artifactory PyPI URL> -t meridian-care .
docker run -p 8501:8501 --env-file .env meridian-care
```
