# MANIFEST: Meridian Care, AI customer-care triage (Python)

The upload form accepts only single files, so this bundle is flattened. Each file's first line gives its **original path** in the project, and the tables below map uploaded names to those paths. The source repository is https://github.com/Mohanaprasadgmp/meridian-care.

**How the pieces fit together**
- There are two agents: a **Customer Interaction Agent** that chats with the tenant, and a **Triage Agent** that classifies, prioritises, routes and resolves.
- They never call each other. They hand off through the database, which acts as a durable queue: requests are claimed atomically, and each reply goes back to the conversation it came from.
- The **LLM proposes and the policy decides.** Priority, routing and every credit amount come from deterministic code, never from the model.

Start with `ARCHITECTURE.md` for the design and `RATIONALE.md` for the key decisions.

## 1. Agent and orchestration logic
| Uploaded file | Original path | What it does |
|---|---|---|
| `agent_orchestrator.py` | `src/meridian/agent/orchestrator.py` | **Triage Agent** loop:<br>• atomic claim (`claim_request`)<br>• bounded tool loop of at most 8 steps (`triage_request`)<br>• safe fallback to Human Triage on any error<br>• parallel batch triage (`triage_all`) and re-queue of provider failures |
| `agent_interaction.py` | `src/meridian/agent/interaction.py` | **Customer Interaction Agent:**<br>• intake tools and reply tool<br>• code-level guards: one ticket per conversation, ticket lookups and listings never open tickets, `_is_clear_yes` before accepting an offer, rate limit<br>• offer acceptance with re-validation (`resolve_offer`)<br>• outcome building and guarded reply composition |
| `agent_worker.py` | `src/meridian/agent/worker.py` | Background worker: claims new chat requests, runs the Triage Agent, posts the reply exactly once (`customer_notified_at`), re-queues stale claims |
| `prompt_triage_v1.md` | `src/meridian/prompts/triage_v1.md` | Versioned system prompt for the Triage Agent |
| `prompt_interaction_v1.md` | `src/meridian/prompts/interaction_v1.md` | System prompt for the Interaction Agent's intake: knowledge-base answers, clarifying questions, offers, ticket questions, short replies, emergencies |
| `prompt_interaction_reply_v1.md` | `src/meridian/prompts/interaction_reply_v1.md` | System prompt for writing the customer reply from the structured triage outcome |

## 2. Tool definitions and guarded execution
| Uploaded file | Original path | What it does |
|---|---|---|
| `tools_definitions.py` | `src/meridian/tools/definitions.py` | Strict JSON schemas for the 8 triage tools:<br>• `record_classification`<br>• `search_request_history`<br>• `get_customer_context`<br>• `lookup_billing_record`<br>• `issue_courtesy_credit` (no amount argument)<br>• the final actions `acknowledge_request`, `route_request`, `close_as_duplicate` |
| `tools_environment.py` | `src/meridian/tools/environment.py` | `ToolEnvironment.dispatch`, which executes every tool call behind guardrails:<br>• enforces the required tool order and exactly one final action<br>• checks duplicate validity<br>• runs the credit rules and creates offers<br>• records blocked calls |
| `agent_interaction.py` | (see above) | Interaction Agent tools (`INTAKE_TOOLS`, `REPLY_TOOLS`):<br>• `search_knowledge_base`<br>• `submit_service_request`<br>• `respond_to_credit_offer`<br>• `add_note_to_request`<br>• `get_ticket_status`<br>• `get_my_requests`<br>• `send_reply` |

## 3. Policy and guardrails (deterministic)
| Uploaded file | Original path | What it does |
|---|---|---|
| `policy_priority.py` | `src/meridian/policy/priority.py` | Weighted signal scoring, giving P1–P4 with the calculation shown to reviewers |
| `policy_routing.py` | `src/meridian/policy/routing.py` | Routing table: category, priority, confidence and signals give the team (emergency and human-triage overrides) |
| `policy_credit_guard.py` | `src/meridian/policy/credit_guard.py` | 8-rule credit check:<br>• tolerance ±max($2, 10%), $50 auto cap, one credit per customer, exact name match<br>• the amount always comes from the record<br>• outcomes: issue / offer / pending approval / deny, with a customer-safe `credit_reason` |
| `models.py` | `src/meridian/models.py` | Enums and Pydantic models: categories, signals, claim kinds, priorities, teams, statuses, action types |

## 4. Persistence code
| Uploaded file | Original path | What it does |
|---|---|---|
| `db.py` | `src/meridian/db.py` | SQLAlchemy 2.0 models:<br>• `requests`, `conversations`, `chat_messages`, `billing_records`<br>• `agent_runs`, `classifications`, `tool_calls`, `actions`<br>• `courtesy_credits`, `overrides`, `users`<br><br>Also: SQLite in WAL mode (Postgres by changing the URL) and an additive migration. |
| `chat.py` | `src/meridian/chat.py` | Conversations and messages with an ownership check on every read and write; ticket status and listing; soft delete of conversations; delivery of worker replies |
| `services.py` | `src/meridian/services.py` | Request creation, plus audited human overrides: change a field, confirm, reopen a duplicate, reverse, approve or reject a credit |
| `ingest.py` | `src/meridian/ingest.py` | Idempotent CSV to database load, with validation |
| `auth.py` | `src/meridian/auth.py` | Accounts: PBKDF2-SHA256 hashes, lockout after 5 failures, logins scoped to a portal (admin or customer) |
| `knowledge.py` | `src/meridian/knowledge.py` | Keyword search over the knowledge base, and next steps per category |
| `knowledge_base.md` | `data/knowledge_base.md` | Knowledge base content (sample policies, marked for replacement with Meridian's real ones) |

## 5. LLM adapters (model-agnostic)
| Uploaded file | Original path | What it does |
|---|---|---|
| `llm_base.py` | `src/meridian/llm/base.py` | The `LLMClient` interface and `LLMResponse` |
| `llm_factory.py` | `src/meridian/llm/__init__.py` | Picks the provider: `company`, `openai`, `claude` or `fake` |
| `llm_openai_client.py` | `src/meridian/llm/openai_client.py` | Company LLM gateway (OpenAI-compatible) and OpenAI: compatibility mode, context budget check, corporate CA bundle |
| `llm_claude.py` | `src/meridian/llm/claude.py` | Anthropic Claude adapter |
| `llm_fake_offline_agent.py` | `src/meridian/llm/fake.py` | Deterministic offline agent for tests and key-less demos. It plays both agents through the same tools and guardrails. |

## 6. Configuration, operations and evaluation
| Uploaded file | Original path | What it does |
|---|---|---|
| `config.py` | `src/meridian/config.py` | All settings and thresholds, read from `.env` (no secrets in code) |
| `cli_main.py` | `src/meridian/__main__.py` | CLI: `init`, `ingest`, `run`, `eval`, `stats`, `check-llm`, `create-user`, `worker`, `seed-demo-customers` |
| `evaluation.py` | `src/meridian/evaluation.py` | Scores results against `gold_labels.csv`: category, priority and action accuracy, P1 recall, credit precision and recall, cost, latency |
| `observability.py` | `src/meridian/observability.py` | JSON logs with personal data redacted, and token cost estimates |

## 7. User interface (Streamlit)
| Uploaded file | Original path | What it does |
|---|---|---|
| `app_streamlit_app.py` | `app/streamlit_app.py` | Landing page (choose a portal, animated login), routing by role, embedded worker |
| `app_customer_chat.py` | `app/customer_chat.py` | Customer chat: fresh conversation on sign-in, live refresh, delete a conversation |
| `app_admin_console.py` | `app/admin_console.py` | Human review: queue, reasoning, tool trace, overrides, credit approval, audit log, insights, customers |
| `app_ui.py` | `app/ui.py` | Shared styling and colours |

## 8. Tests (99 pytest tests, offline and deterministic)
| Uploaded file | Original path | What it covers |
|---|---|---|
| `tests_conftest.py` | `tests/conftest.py` | Fresh database per test; a frozen copy of the data |
| `tests_test_policy.py` | `tests/test_policy.py` | Priority, routing and every credit rule, including the traps in the data |
| `tests_test_agent.py` | `tests/test_agent.py` | Tool protocol enforcement, a single final action, output money guards, duplicate rules, failure and refusal fallbacks, the full offline run |
| `tests_test_chat.py` | `tests/test_chat.py` | Two-agent hand-off, concurrent claims, conversation ownership, the offer flow, knowledge-base answers, one ticket per conversation, ticket lookups and listings, deleted conversations |
| `tests_test_services.py` | `tests/test_services.py` | Audited overrides and credit decisions |
| `tests_test_auth.py` | `tests/test_auth.py` | Hashing, lockout, portal scoping |
| `tests_test_openai_adapter.py` | `tests/test_openai_adapter.py` | OpenAI and company gateway adapter behaviour |

## 9. Documents
| Uploaded file | Original path | What it is |
|---|---|---|
| `ARCHITECTURE.md` | `docs/ARCHITECTURE.md` | Architecture, guardrails, production readiness, known limitations |
| `README.md` | `README.md` | Setup, run and demo steps |
| `RATIONALE.md` | (submission only) | Key decisions, underspecified items, corrections to AI suggestions, next steps |
| `MANIFEST.md` | (submission only) | This file |

The input data (`storage_requests.csv`, `storage_billing_records.csv`) comes from the use case and is not re-uploaded, because the form accepts only `.py`, `.java` and `.md` files.
