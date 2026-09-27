# Meridian Care: AI Triage Agent Architecture

## 1. Problem
Meridian Self Storage customer care receives a continuous stream of tenant requests. Staff read each one and decide what it is, how urgent it is, who owns it, and whether the tenant is owed money. This is slow and inconsistent.

We automate that front desk with an agent. It is allowed to act on its own (acknowledge, route, close duplicates, issue small courtesy credits), but only inside guardrails that code enforces. A human can see and override everything.

The seed data (100 requests, 29 billing records) is deliberately adversarial:

| Trap | Example | Required behaviour |
|---|---|---|
| Goodwill demand | Anjali: "$210 back would make this right" (record 33.85) | Never pay the claimed amount; route to a human |
| Record shows no discrepancy | Delphine claims $8, Callum claims $40 (record 0.00) | No credit |
| Tenant owes money | Rosalind "charged twice?" (record −338.90) | No credit |
| Amount above auto-limit | Percival ~$310 (record 308.60) | A human must approve |
| Name collision | Marguerite *Effiong* vs Marguerite *Solheim* | Exact-name match only |
| True duplicate | Renwick TR-6039 vs his open TR-6038 | Auto-close and link |
| False duplicates | Four different tenants, identical text | Not duplicates |
| Urgency that is only loud | "URGENT", Business Elite tier, "no rush" | Rank by real harm and deadline |
| Real emergency | Sprinkler dripping while the tenant is leaving in an hour; active water plus mold | P1, sent to the emergency team |
| Multilingual | Spanish double-charge request | Classify correctly and reply in Spanish |
| Vague claim | "not sure by how much", "a couple dollars", hearsay | Not checkable, so no auto-credit |

## 2. Architecture

```mermaid
flowchart LR
    CSV[(Seed CSVs)] --> ING[Ingest<br/>validate + idempotent]
    ING --> DB[(SQL database)]
    DB --> ORCH[Orchestrator<br/>bounded loop, workers]
    subgraph AGENT[Triage agent: one LLM with tools]
      LLM[Claude<br/>versioned prompt]
      ENV[Tool environment<br/>protocol + guardrails]
      POL[Policy engine<br/>priority / routing / credit]
      LLM -- tool_use --> ENV
      ENV -- tool_result / blocked --> LLM
      ENV --> POL
    end
    ORCH --> AGENT
    ENV -- actions, credits, trace --> DB
    DB --> UI[Streamlit review console<br/>reasoning, trace, overrides]
    UI -- audited overrides --> DB
    DB --> EVAL[Eval harness<br/>vs gold labels]
```

### Why one agent, not several
Triage is one sequential decision per ticket: understand it, gather evidence, decide, act. There are no independent sub-problems to run in parallel, and every step depends on the one before.

Splitting the work into classifier, billing and router agents would add hand-offs, extra LLM calls, latency, cost and failure points without adding accuracy. All the specialist knowledge (billing rules, routing tables, priority weights) is deterministic, so it lives in code as tools and policy rather than in extra agents.

We chose multi-agent only where it pays off, which is nowhere in this workflow. The design still scales: parallelism comes from running many single-agent workers at once, one per ticket.

### Core principle: the LLM proposes, the policy decides
| The LLM does (language and judgement) | Deterministic code does (money and state) |
|---|---|
| Classify into the 5 categories, with confidence | Compute priority P1–P4 from the signals (auditable weights) |
| Extract urgency signals from the text | Route to a team from the category, priority and confidence |
| Extract the claimed amount, the claim type and whether the claim is specific | Decide credit eligibility and the credit amount (always the verified record) |
| Choose which tools to call and when | Enforce the tool order, a single final action, and duplicate validity |
| Write reasoning for staff and messages for tenants | Validate tenant messages (no invented amounts or promises) |

### Agent workflow for one request
```mermaid
sequenceDiagram
    participant L as LLM
    participant E as Tool environment
    participant P as Policy
    L->>E: record_classification(category, confidence, signals, claim...)
    E->>P: score_priority + route
    E-->>L: priority, team, required_next_steps
    L->>E: search_request_history()
    E-->>L: same-tenant history + similarity
    alt confidence < 0.75
      L->>E: get_customer_context()
      L->>E: record_classification(revised)
    end
    alt Billing with a specific amount
      L->>E: lookup_billing_record()
      L->>E: issue_courtesy_credit()
      E->>P: evaluate_credit (8 rules)
      E-->>L: issue / pending_approval / deny + rule results
    end
    L->>E: exactly ONE of acknowledge_request | route_request | close_as_duplicate
    E-->>L: done (any further action is blocked)
```

### Tools
| Tool | Type | Key guardrail |
|---|---|---|
| `record_classification` | reasoning capture | Strict schema with enums; policy returns the priority, team and required next steps |
| `search_request_history` | data (tool #1) | Scoped to the current tenant by exact name; no identifier arguments |
| `get_customer_context` | data (tool #2) | **Required** when confidence < 0.75 |
| `lookup_billing_record` | data | Allowed only for Billing requests; **required** for a specific billing claim |
| `issue_courtesy_credit` | money | Takes no amount argument; the 8-rule policy check; cap $50 |
| `acknowledge_request` | final action | Valid only after a credit was issued (resolves the request) |
| `route_request` | final action | The team comes from policy, not from the LLM |
| `close_as_duplicate` | final action | Same tenant, earlier, still open; not P1 into a lower priority |

## 3. Guardrails (defence in depth)
1. **Money:**
   - The credit amount always comes from the verified billing record. The tool has no amount argument, so neither prompt injection nor model error can change it.
   - Eight rules must all pass: Billing category; confidence ≥ 0.8; eligible claim type (never goodwill); a specific claim; exact-name record; record > 0; claimed amount within ±max($2, 10%) of the record; no prior credit.
   - Above the $50 cap, the credit becomes `pending_approval` for a human.
   - A process-wide lock plus a unique constraint prevent double payouts.
2. **Protocol:** the agent cannot skip required steps (classify → history → context when unsure → billing lookup when a claim is made). Exactly one final action is allowed; anything after it is blocked.
3. **Output:**
   - Tenant messages are limited to 600 characters.
   - A message may not mention any dollar figure except an issued credit.
   - Promises such as "we will refund" are blocked unless a credit was issued.
4. **Prompt injection:**
   - Tenant text is fenced as `<tenant_request>` and the prompt labels it as untrusted.
   - Data tools take no identifiers (least privilege).
   - Money tools take no amounts.
   - Every tool schema is strict (`additionalProperties: false`).
5. **Uncertainty:** low confidence (below 0.75) forces a context lookup. If final confidence is below 0.55, the request goes to **Human Triage** and no duplicate close or credit is allowed.
6. **Safety:** a P1 with active damage or a safety risk goes to **Facilities Emergency Response** and cannot be closed as a duplicate into a lower-priority ticket.
7. **Bounded execution and safe failure:**
   - Maximum of 8 steps.
   - SDK timeouts and retries with backoff.
   - Server-side refusal fallback.
   - Any error, refusal, exhausted step budget, or missing final action results in a `fallback_route` to Human Triage. A request is never dropped and never auto-actioned on a failure path.
8. **Transparency:** blocked attempts are returned to the model as errors it can correct, and stored with `allowed=False` so reviewers see what was prevented.

## 4. Quality of AI output
- **Structured output.** A strict tool schema with enums means every classification is machine-valid, never free text to parse.
- **Explainable.** Reviewers see the reasoning, a one-line summary, the urgency signals, and the exact priority arithmetic (`base 15 | active_property_damage +60 | time_critical_deadline +30 = 105 -> P1`) and routing rule.
- **Self-correcting.** When a tool shows the model it was wrong (for example, customer context), it re-records its classification. Every revision is kept.
- **Measured.**
  - `data/gold_labels.csv` holds a hand-labelled expected outcome for all 100 requests. Where a request is ambiguous, several answers are accepted.
  - Metrics: category, priority and action accuracy; **P1 recall**; **credit precision (target 100%: zero wrong payouts)**; strict credit recall; fallbacks; guardrail blocks; steps; latency; cost.

## 5. Production readiness
| Concern | Implementation |
|---|---|
| Model portability | `LLMClient` interface; Claude and offline implementations; model, effort and provider set by env config |
| Reliability | SDK retries and backoff on 429/5xx, timeouts, refusal fallback, safe fallback to humans |
| Idempotency | Ingest skips existing IDs; only untriaged requests are processed; unique credit per request; one credit per customer |
| Concurrency | Thread-pool workers with one DB session each; money operations serialised |
| Observability | JSON logs with PII fields redacted; each run stores model, prompt version, steps, tokens, latency and cost; a full tool trace per request |
| Auditability | Every agent action and human override is stored with actor, time and reason |
| Cost | Prompt caching on the fixed system prompt and tools; `effort=medium` for short-horizon triage; per-run cost tracking |
| Config and secrets | `pydantic-settings` reading `.env`; no secrets in code; policy thresholds in config, not in prompts |
| Data | SQLAlchemy 2.0; SQLite (WAL) for the demo; Postgres by changing the URL |
| Deployment | Dockerfile (non-root user, healthcheck); `run.ps1` |
| Testing | 38 pytest tests: every data trap, protocol enforcement, output guardrails, failure modes, overrides, and a full offline end-to-end run |
| Change safety | Versioned prompts (`prompts/triage_v1.md`); the eval gates any change to the prompt or model |

### Scaling path
- **Throughput:** Claude Message Batches (50% cost) for backlogs.
- **Architecture:** a queue such as SQS or Redis in front of the workers; Postgres; OpenTelemetry export of the run traces.
- **Access:** role-based access in the console.
- **Monitoring:** drift checks by sampling human overrides as new gold labels.

## 6. Human in the loop
The review console provides:
- a priority-sorted queue with filters
- for each request: the tenant message, the LLM reasoning, the priority calculation, a step-by-step tool trace (blocked calls highlighted), the actions taken, and the tenant messages sent
- overrides for category, priority, team and status, where a reviewer name and reason are mandatory
- confirming the agent's decision
- reopening a closed duplicate
- reversing an issued credit, and approving or rejecting pending credits
- an audit log and a metrics tab with eval scores

## 7. Known limitations
- Requests that fit none of the 5 categories (for example, a pest schedule or a boat trailer) are forced into the closest one with low confidence. In production a sixth "General Inquiry" category would be better, but the brief fixes the five.
- Duplicate detection relies on the LLM's judgement plus structural guards. Very large histories would need retrieval (embeddings) instead of returning the whole history.
- The offline keyword agent exists to test the plumbing and to demo without a key. It was written after reading this dataset, so its scores are **not** a meaningful baseline.
