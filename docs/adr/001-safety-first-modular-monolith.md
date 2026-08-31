# ADR-001: Safety-first PostgreSQL modular monolith

- Status: Accepted
- Date: 2026-08-31

## Context

The prototype must demonstrate progressive and predictive dialing for human collections agents under concurrency, provider disorder, crashes, and outages. Correctness is weighted above UI or infrastructure breadth.

## Decision

Use Python 3.12, FastAPI, SQLAlchemy, PostgreSQL 16, Alembic, a database worker, two deterministic provider simulators, pytest/Testcontainers, and Docker Compose. PostgreSQL is the sole source of truth. No Redis, Celery, Kafka, dashboard, authentication, or real telecom integration is required for this prototype.

### Predictive policy

Pacing produces an expected-value proposal. The mandatory Safety Controller uses a one-sided 95% Wilson upper confidence bound for answer probability and finds the largest batch whose exact binomial probability of answers exceeding human capacity is within policy.

- Default overload probability: 0.5% per decision.
- Absolute ceiling: 1% per decision; campaign configuration cannot exceed it.
- Operators can choose any stricter value to 0%.
- 0% produces pure progressive allocation and has unit plus PostgreSQL integration tests.
- Cold start (under 30 attempts), stale presence, provider degradation, or rapid agent loss forces progressive.

The 0.5%/1% value is a **per-decision probability**, not cumulative campaign probability. At-least-one-overload probability compounds over repeated decisions. This is a known prototype simplification. Production needs a campaign-level risk budget that spends/replenishes exposure and tightens decisions after incidents.

### Allocation and locks

- Borrowers: campaign priority, eligible retry time, oldest queued, stable ID.
- Humans: compatible campaign/language, longest idle, stable ID.
- Debt amount and inferred behavior are excluded to avoid an unexplained collections policy and fairness concerns in a prototype.
- Selection uses `SELECT ... FOR UPDATE SKIP LOCKED`.
- Universal lock order is agent first, borrower second whenever both are needed.
- One transaction reserves resources and creates the intent.
- A pre-commit crash leaves no reservation because PostgreSQL rolls back.

Progressive reserves agent and borrower together. Predictive reserves borrower first and assigns a human on observed `ANSWERED`; pre-reserving one human per predictive call would collapse predictive into progressive.

### Recovery and provider correctness

Workers claim intents with `SKIP LOCKED` and a 30-second lease. Lease expiry can double-claim. The lease is a liveness bound; correctness comes from querying the original provider using a stable provider-local idempotency key. Three failed claims lead to terminal failure, ownership-checked release, incident, and manual review.

Failover happens only after the original provider conclusively reports no call for its key. Keys are never portable across providers. Inconclusive reconciliation becomes terminal `AMBIGUOUS`; no second borrower call is risked.

### Event consistency

The event inbox is append-only. Unique constraints and `INSERT ... ON CONFLICT DO NOTHING` deduplicate ID/fingerprint races. Event insertion, transition, human allocation/release, and incidents share one transaction. Explicit transitions replace ordinal comparisons. Terminal states absorb later events. Skipped `ANSWERED` is inferred and excluded from observed answer statistics.

### Human presence

Workstations heartbeat every 5 seconds. Graceful pause/offline is immediate; silent disappearance is detected at 15 seconds. Ringing cancellation holds ownership for a 10-second reconciliation lease, then force-releases. Call-intent reconciliation precedes heartbeat release, preventing double-release paths.

## Consequences

Advantages:

- Small, reproducible architecture with genuine PostgreSQL behavior.
- Pacing structurally cannot bypass safety or place calls.
- Concurrency/crash arguments map directly to transactions, constraints, and tests.
- Provider disorder is deterministic and reproducible.

Costs and limitations:

- PostgreSQL is queue, state store, and inbox; connections are the likely first bottleneck.
- Predictive risk is bounded, not eliminated. True zero-abandonment requires progressive mode, guaranteed overflow, or telecom holding.
- In-process simulators model external provider idempotency but do not themselves persist across process restart.
- No auth/RBAC. Production needs authentication, roles, PII encryption, calling-window/DND enforcement, metrics, alerts, and retention controls.
- The initial migration creates v1 metadata; later revisions should use explicit incremental operations.

## Alternatives rejected

- Fixed `capacity / answer_rate`: ignores uncertainty and variance.
- ML: no credible dataset and harder to defend in the timebox.
- Redis/Celery: extra consistency boundary without helping the central proof.
- SQLite tests: cannot validate PostgreSQL locking, `SKIP LOCKED`, or `ON CONFLICT`.
- Real Plivo: optional and blocked by business-domain-email signup requirements.
