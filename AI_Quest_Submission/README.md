<!-- Original path: README.md -->
# Meridian Care: AI customer-care triage

An AI solution for **Meridian Self Storage** customer care, built in Python. Tenants chat with an assistant, and their issues are classified, prioritised, routed and, where the rules allow, resolved automatically (for example a verified billing credit). Staff review and can override every decision in an admin console, and everything is stored in a database.

It uses two agents that hand work to each other through the database:

| Agent | What it does |
|---|---|
| **Customer Interaction Agent** | Chats with a signed-in tenant. It answers general questions from a knowledge base, opens one ticket per conversation, looks up ticket status and lists the tenant's tickets. It also relays the triage result back into the chat. |
| **Triage Agent** | Picks up each new request. It classifies it into one of 5 categories, checks the tenant's history, pulls extra context when unsure, sets a priority and routes it. It then acknowledges it, closes it as a duplicate, or verifies a billing discrepancy and issues a courtesy credit. |

The model proposes and the code decides. Priority, routing and every credit amount are enforced by fixed rules in code, never by the model. Design details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and the presenter walkthrough is in [demo/DEMO_SCRIPT.md](demo/DEMO_SCRIPT.md).

---

## Contents
1. [Before you start](#1-before-you-start)
2. [Set up and run (Windows)](#2-set-up-and-run-windows)
3. [Try it](#3-try-it)
4. [Command reference](#4-command-reference)
5. [macOS / Linux](#5-macos--linux)
6. [Configuration (.env)](#6-configuration-env)
7. [Installing packages from Artifactory](#7-installing-packages-from-artifactory)
8. [Docker](#8-docker)
9. [Troubleshooting](#9-troubleshooting)
10. [Project layout](#10-project-layout)

---

## 1. Before you start

You need:
- **Python 3.10 or newer.** Check with `python --version`.
- **Git**, to clone the repository.
- **An AI model**, which is any one of these:

| Option | When to use it | What you need |
|---|---|---|
| `fake`, the offline agent | Trying the app with no key, running tests, demos without network | Nothing |
| `company`, the LLM@CIB gateway | The real experience inside the company network | Your personal key from the Developer Assistant page |
| `openai` | Outside the company network | An OpenAI API key |
| `claude` | Outside the company network | An Anthropic API key |

The **offline agent** is a set of built-in keyword rules that stands in for the AI model. Everything else still runs for real: the database, the worker, the priority and routing rules, the credit checks and the guardrails. It only understands the phrasings it has rules for, so use a real model to judge how well the agent understands customers.

---

## 2. Set up and run (Windows)

Run these in **PowerShell**, not Command Prompt, from the project folder.

### Step 1: Get the code
```powershell
git clone https://github.com/Mohanaprasadgmp/meridian-care.git
cd meridian-care
```
Already cloned? Get the latest with `git pull`.

### Step 2: Allow the run script for this window
Windows blocks unsigned scripts by default. This only affects the current PowerShell window:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```
You need to run this again each time you open a new PowerShell window.

### Step 3: Create your settings file
```powershell
copy .env.example .env
```
`.env` holds your settings and keys. It is git-ignored and must never be committed.

If your organisation blocks public PyPI, fill in `ARTIFACTORY_PYPI_URL` in `.env` now (see [section 7](#7-installing-packages-from-artifactory)).

### Step 4: Install
```powershell
.\run.ps1 setup
```
This creates a `.venv` folder and installs `requirements.txt` into it. It ends with `Setup complete.`

### Step 5: Choose the AI model
Open `.env` in any editor and set **one** of these:

**Offline, no key needed:**
```ini
MERIDIAN_LLM_PROVIDER=fake
```
**Company LLM:**
```ini
MERIDIAN_LLM_PROVIDER=company
MERIDIAN_COMPANY_API_KEY=<your personal key>
```
**OpenAI:**
```ini
MERIDIAN_LLM_PROVIDER=openai
OPENAI_API_KEY=<your key>
```

For a real model, check the connection before going further:
```powershell
.\run.ps1 cli check-llm
```
You should see `OK: connected, authenticated, tool calling works`. If the key is missing, it says `FAILED: RuntimeError: MERIDIAN_COMPANY_API_KEY is not set in .env`.

### Step 6: Create the database
```powershell
.\run.ps1 reset
```
This creates `meridian.db` and loads the sample requests and billing records from `data/`. Running it again wipes all requests, chats and credits, but **login accounts are kept**.

### Step 7: Create logins
An **administrator** account. You are asked to type the password (at least 8 characters) twice, and it is stored hashed:
```powershell
.\run.ps1 cli create-user admin --role admin
```
**Customer** accounts. The quickest way is four demo tenants with random passwords:
```powershell
.\run.ps1 cli seed-demo-customers
```
The passwords are printed once, so copy them. The tenants are `desmond`, `naomi`, `perpetua` and `corinne`.

You can also create your own customer. `--display-name` must match the tenant's name **exactly** as it appears in `data/storage_billing_records.csv`, because that's how billing claims are verified:
```powershell
.\run.ps1 cli create-user jane --role customer --display-name "Jane Doe" --tier Standard
```
Running `create-user` again for an existing username resets that user's password.

### Step 8: Start the app
```powershell
.\run.ps1 app
```
Open **http://localhost:8501**. Choose **Customer** or **Administrator**, then sign in. Stop the app with `Ctrl+C`.

The app runs its own background triage worker, so chat requests are processed without any extra command.

### Step 9 (optional): Triage the CSV requests and score the agent
The sample requests loaded in step 6 wait in the admin console until you triage them:
```powershell
.\run.ps1 triage   # run the Triage Agent over all pending CSV requests
.\run.ps1 eval     # compare the results with data/gold_labels.csv -> eval/latest_eval.json
```

### Step 10 (optional): Run the tests
```powershell
.\run.ps1 test
```
There are 99 tests. They use the offline agent and a throwaway database, so they need no key and don't touch your data.

---

## 3. Try it

### As a customer
Sign in as one of the demo tenants and try these:

| Sign in as | Type | What happens |
|---|---|---|
| `perpetua` | `What time do the gates open?` | Answered at once from the knowledge base. No ticket is opened. |
| `desmond` | `My autopay this month was $22.40 more than my usual rent. Please fix it.` | Ticket opened. A few seconds later: *courtesy credit of $22.40 applied, ticket resolved*. The claim matches his verified billing record. |
| `naomi` | `I was overcharged $50 on my storage bill this month, please refund it` | The record only verifies **$37.15**, so the assistant offers $37.15 and asks yes or no. Reply `yes` and the credit is issued; reply `no` and it goes to the Billing Team. |
| any | `My gate code stopped working` | Ticket routed to the right team, with next steps. |
| any | `what are all my tickets` / `any update on TR-6061?` | Lists the tenant's tickets, or shows one ticket's status. No new ticket is opened. |

Other things to try in the customer portal:
- **New conversation** starts a separate thread. Each sign-in also starts on a new conversation.
- The **bin icon** next to a conversation removes it from the list. Its tickets are not cancelled.

Ticket numbers depend on what is already in your database.

### As an administrator
- **Queue:** every request with its priority, category, team and status. Filter by source (`chat` or `csv`).
- **Review:** the LLM's reasoning, the full tool trace, actions taken and the customer chat. From here you can override the category, priority or team, and approve or reject credits.
- **Credits:** every credit, with the amount claimed next to the amount verified.
- **Audit log:** every action taken by the agents and by staff.
- **Insights:** requests by priority, team and category, human overrides, and what the guardrails blocked.
- **Customers:** customer accounts.

---

## 4. Command reference

| Command | What it does |
|---|---|
| `.\run.ps1 setup` | Create `.venv` and install packages |
| `.\run.ps1 reset` | Recreate the database and load the CSVs. Login accounts are kept. |
| `.\run.ps1 app` | Start the web app on http://localhost:8501 |
| `.\run.ps1 triage` | Triage all pending CSV requests |
| `.\run.ps1 eval` | Score against the gold labels |
| `.\run.ps1 worker` | Run an extra background worker, for more throughput |
| `.\run.ps1 test` | Run the test suite |
| `.\run.ps1 cli <command>` | Run any CLI command (listed below) |

CLI commands, used as `.\run.ps1 cli <command>`:

| Command | What it does |
|---|---|
| `check-llm` | Test the connection, key, model and tool calling |
| `create-user <name> --role admin\|customer [--display-name "..."] [--tier Standard\|"Business Elite"]` | Create a login, or reset its password |
| `seed-demo-customers` | Create the four demo tenant logins |
| `init [--reset]` / `ingest` | Create the tables, or wipe them / load the CSVs |
| `run [--id TR-6057] [--workers N] [--retry-failed]` | Triage all pending requests or one request. `--retry-failed` re-queues requests that failed on provider errors. |
| `stats` | Print a summary of the database |
| `worker [--once]` | Run the triage worker |

---

## 5. macOS / Linux

`run.ps1` is for Windows. On macOS or Linux, run the same steps directly:
```bash
git clone https://github.com/Mohanaprasadgmp/meridian-care.git && cd meridian-care
cp .env.example .env                       # then edit it (step 5 above)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # add --index-url <Artifactory URL> if needed
export PYTHONPATH=src
python -m meridian check-llm               # skip when MERIDIAN_LLM_PROVIDER=fake
python -m meridian init --reset && python -m meridian ingest
python -m meridian create-user admin --role admin
python -m meridian seed-demo-customers
streamlit run app/streamlit_app.py         # http://localhost:8501
pytest -q                                  # tests
```

---

## 6. Configuration (.env)

All settings live in `.env`, which you copy from [.env.example](.env.example). Real environment variables take precedence over `.env`. For example, `$env:MERIDIAN_LLM_PROVIDER="fake"` in PowerShell switches to the offline agent for that window only.

| Setting | Default | Purpose |
|---|---|---|
| `MERIDIAN_LLM_PROVIDER` | `company` | `company`, `openai`, `claude` or `fake` |
| `MERIDIAN_COMPANY_API_KEY` | (empty) | Company LLM key |
| `MERIDIAN_COMPANY_BASE_URL` | `https://api.llm.cib.echonet/v1/openai` | Company gateway |
| `MERIDIAN_COMPANY_MODEL` | `gpt-oss-120b-ITG` | Company model |
| `MERIDIAN_COMPANY_CONTEXT_TOKENS` | `131000` | The chosen model's real context size |
| `MERIDIAN_COMPANY_CA_BUNDLE` | (empty) | Corporate root CA `.pem`, if you get TLS errors |
| `OPENAI_API_KEY` / `MERIDIAN_OPENAI_MODEL` | `gpt-4.1` | OpenAI |
| `ANTHROPIC_API_KEY` / `MERIDIAN_MODEL` | `claude-opus-5` | Claude |
| `MERIDIAN_CREDIT_AUTO_CAP` | `50` | Credits above this amount need an admin's approval |
| `MERIDIAN_WORKERS` | `4` | Parallel workers for `.\run.ps1 triage` |
| `MERIDIAN_DATABASE_URL` | local `meridian.db` (SQLite) | For example `postgresql+psycopg://user:pass@host/meridian` |

---

## 7. Installing packages from Artifactory

If your organisation blocks public PyPI, set these in `.env` **before** `.\run.ps1 setup`:

| Setting | Purpose |
|---|---|
| `ARTIFACTORY_PYPI_URL` | Your PyPI repo, e.g. `https://<host>/artifactory/api/pypi/<repo>/simple`. If it needs a login: `https://<user>:<token>@<host>/artifactory/api/pypi/<repo>/simple` |
| `ARTIFACTORY_CA_BUNDLE` | Corporate root CA `.pem`, if pip reports SSL certificate errors |
| `ARTIFACTORY_NPM_URL` | Your npm repo. Only needed to rebuild the slides with `.\run.ps1 deck-setup`. |
| `PYTHON_EXE` | Full path to Python 3.10+, if `python` is not on PATH |

Leave them blank to use public PyPI. Without the script, run `pip install --index-url <your-url> -r requirements.txt`.

---

## 8. Docker
```bash
docker build --build-arg PIP_INDEX_URL=<your Artifactory PyPI URL> -t meridian-care .
docker run -p 8501:8501 --env-file .env meridian-care
```
Leave out `--build-arg` to use public PyPI.

---

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `run.ps1 cannot be loaded ... not digitally signed` or `running scripts is disabled` | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that window (step 2), or `Unblock-File .\run.ps1` once. |
| `.\run.ps1 : The term ... is not recognized` | You're in Command Prompt or the wrong folder. Open **PowerShell** and `cd` into `meridian-care`. |
| `python` is not found, or the version is too old | Install Python 3.10+, or set `PYTHON_EXE` in `.env` to its full path. |
| `pip install failed` | Check `ARTIFACTORY_PYPI_URL`, the credentials in it, and `ARTIFACTORY_CA_BUNDLE`. |
| `check-llm` fails with `... is not set in .env` | Add the key for your chosen provider to `.env` (step 5). |
| `check-llm` fails with a connection or SSL error | The company gateway is only reachable on the company network. Set `MERIDIAN_COMPANY_CA_BUNDLE` for TLS errors. |
| Rate-limit (429) errors during `.\run.ps1 triage` | Use fewer workers with `.\run.ps1 cli run --workers 1`, then `.\run.ps1 cli run --retry-failed`. |
| The page says *No accounts exist yet* | Create an admin (step 7), then refresh. |
| *Invalid username or password* | Make sure you picked the right portal: customers can't sign in as administrator, and the other way round. After 5 wrong tries the account locks for 5 minutes. |
| The chat never shows the result | The app must stay running, because its worker processes the queue. Check the terminal for errors. |
| A credit is not issued for my own customer | The customer's `--display-name` must match the name in `data/storage_billing_records.csv` exactly. |
| Port 8501 is in use | Stop the other app, or run `.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py --server.port 8502`. |

---

## 10. Project layout

| Path | Purpose |
|---|---|
| `app/streamlit_app.py` | Entry point: landing page, sign-in, routing by role, embedded worker |
| `app/customer_chat.py` | Customer portal (chat) |
| `app/admin_console.py` | Admin console: queue, review, overrides, credits, customers |
| `src/meridian/agent/interaction.py` | Customer Interaction Agent |
| `src/meridian/agent/orchestrator.py` | Triage Agent: bounded tool loop with a safe fallback |
| `src/meridian/agent/worker.py` | Background worker: claims new requests atomically and posts the reply to the chat |
| `src/meridian/tools/` | Tool definitions and guarded tool execution |
| `src/meridian/policy/` | Fixed rules for priority, routing and credits |
| `src/meridian/llm/` | Model adapters: company gateway, OpenAI, Claude, offline agent |
| `src/meridian/prompts/` | Versioned system prompts |
| `src/meridian/chat.py`, `auth.py`, `knowledge.py` | Conversations, logins (PBKDF2-hashed), knowledge-base search |
| `src/meridian/db.py` | Database models (SQLite by default, Postgres-ready) |
| `data/` | Sample requests, billing records, gold labels and the knowledge base (sample content) |
| `tests/` | Test suite, using a frozen copy of the data in `tests/fixtures/` |
| `docs/ARCHITECTURE.md` | Architecture and design decisions |
| `deck/`, `demo/` | Presentation and demo script |
