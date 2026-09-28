# Rationale

## Key decisions
- **The LLM proposes, the policy decides.** The model classifies requests and writes text. Priority, routing and credits are deterministic code, and the credit tool has no amount argument, so what is paid always comes from the verified billing record.
- **One Triage Agent plus a Customer Interaction Agent.** Triage is one sequential decision, so more agents would add cost and failure points without adding accuracy. Talking to customers is a different job. The two agents hand off only through the database, which acts as a durable queue with atomic claims, so many customers can chat at once and each sees only their own replies.
- **Model-agnostic with safe failure.** One interface serves the company LLM, OpenAI, Claude and an offline test agent. Any error sends the request to Human Triage, so nothing is dropped and nothing is auto-actioned.

## Underspecified items I resolved
- **Verified discrepancy:** an exact-name record above $0, and a specific claim within ±max($2, 10%) of it.
- **Credit limits:** automatic up to $50, above that a human approves; one credit per customer; no goodwill or vague claims.
- **Uncertainty:** confidence below 0.75 forces the context tool, and below 0.55 the request goes to a human.
- **Priority and duplicates:** priority is based on real harm and deadlines, not the "URGENT" wording or account tier. Duplicates must be the same tenant and still open.
- **Over-claims in chat:** offer the verified amount and let the customer confirm.

## Where I questioned or corrected the AI assistant
- I asked how a refund amount was being confirmed, and checked that it comes only from the billing record.
- The first chat agent sent almost everything to the Billing Team. I pushed it to resolve more: verified-amount offers, knowledge-base answers and specific next steps.
- Testing showed three problems:
  - Follow-ups, "yes"/"S"/"thank you", ticket-number questions and "what tickets do I have" each opened new tickets.
  - A status question was read as accepting an offer.
  - An OpenAI key was left behind after the switch to the company LLM.

  I required one ticket per conversation, replies that use the conversation's context, and the key removed. The rules are now enforced in code, with tests.

## With more time
- Run the full evaluation on the company model and tune the prompts.
- Use the real Meridian policies with semantic search.
- Move to Postgres with a queue, add single sign-on for staff, and load-test the chat.
- Add a "General Inquiry" category.
