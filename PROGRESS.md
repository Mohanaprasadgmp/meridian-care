# Progress Tracker: AI Talent Quest Round 3 (Meridian Care)

Judged on: Architecture · Agent design & guardrails · AI output · Production readiness. Language: Python.

| # | Phase | Status | Output |
|---|---|---|---|
| 0 | Understand the use case and data traps | Done | `USE_CASE_SUMMARY.txt` |
| 1 | Scaffold, config, DB, ingest | Done | `src/meridian/{config,db,ingest,models}.py` |
| 2 | Policy layer (priority, routing, credit guard) | Done | `src/meridian/policy/` |
| 3 | Tools and guarded environment | Done | `src/meridian/tools/` |
| 4 | LLM clients (Claude and offline), prompt v1, orchestrator | Done | `src/meridian/llm/`, `prompts/`, `agent/` |
| 5 | Gold labels (100 requests) and eval harness | Done | `data/gold_labels.csv`, `evaluation.py` |
| 6 | Tests | Done (38 passing) | `tests/` |
| 7 | Streamlit review console | Done (headless AppTest, no exceptions) | `app/streamlit_app.py` |
| 8 | Production files (Docker, env, runner) | Done | `Dockerfile`, `.env.example`, `run.ps1` |
| 9 | Architecture doc | Done | `docs/ARCHITECTURE.md` |
| 10 | Demo script | Done | `demo/DEMO_SCRIPT.md` |
| 11 | Slide deck (13 slides, validated, visual QA) | Done | `deck/Meridian_AI_Triage.pptx` (source: `deck/build_deck.js`) |
| 12 | **Real Claude run, eval, prompt iteration** | **Blocked: needs ANTHROPIC_API_KEY** | `eval/latest_eval.json` |

## Decisions log
- Single agent plus a deterministic policy engine (see ARCHITECTURE §2). Multi-agent was rejected: sequential workflow, no parallel sub-problems.
- Model: `claude-opus-5` at effort `medium`, behind a swappable `LLMClient` interface.
- Credit amount always comes from the billing record. Auto-cap is $50; above it, a human approves.
- Off-taxonomy requests: closest category with low confidence, which triggers the context tool and Human Triage if confidence stays low.
- The offline keyword agent is a test fixture, not a baseline: it was written after reading this data.

## Next steps
1. Add `ANTHROPIC_API_KEY` to `.env` and set `MERIDIAN_LLM_PROVIDER=claude`.
2. `.\run.ps1 reset; .\run.ps1 triage; .\run.ps1 eval`
3. Review the misses and tune `prompts/triage_v2.md` if needed. Record the final numbers in the deck.
