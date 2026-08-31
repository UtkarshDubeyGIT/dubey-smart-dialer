# Failure scenarios

`make simulate` executes these stories against ephemeral PostgreSQL using the production services. Each generated report entry is marked `executed_postgresql_production_path`; seeded reports exclude random row IDs so repeated runs are byte-stable.

## Worker crash

- Before allocation commit: PostgreSQL rolls back agent, borrower, and intent; zero reservation remains.
- After commit but before provider call: the durable intent remains claimable.
- Lease owner crashes: after 30 seconds another worker claims with `FOR UPDATE SKIP LOCKED`.
- Lease expires mid-flight: double-claim is possible. Recovery queries the original provider using its provider-local idempotency key. This can waste a lookup but cannot create a second call.
- After three claims: terminal `FAILED`, ownership-checked release, incident, manual review.

The lease is a liveness bound. Provider idempotency is the correctness guarantee.

## Provider outage

- Three consecutive initiation timeouts, or at least 30% failures in the latest 20 attempts after a minimum sample of 10, opens the circuit for 30 seconds.
- Worker outcomes update a PostgreSQL provider-health row in the same transaction as intent processing; API and worker processes therefore share the same circuit state.
- Existing calls remain tracked; an initiation outage does not mean live calls ended.
- Pacing reads the campaign provider's persisted circuit automatically; callers cannot supply a health override. Any degradation forces predictive mode to progressive.
- An intent claimed while its circuit is open is deferred without consuming a processing attempt or invoking an alternate provider.
- After 30 seconds one lock-owning worker runs a provider health/status probe. A successful probe closes the circuit before the waiting borrower call proceeds; a failed probe reopens the cooldown. No borrower is called merely as a probe.
- Circuit state and recent failure rate are visible at `GET /v1/provider-health`.
- Before failover, the original provider is queried with its own key. Confirmed absence permits an alternate with a new provider-specific key. Inconclusive reconciliation becomes `AMBIGUOUS` and manual review.
- Retry delay is bounded exponential backoff with jitter.

## Human-agent availability drop

- Explicit pause/offline: immediate.
- Silent crash/network loss: 15 seconds from last heartbeat, plus polling granularity.
- More than 20% loss within 10 seconds forces progressive fallback.
- Ringing: cancel and hold ownership for at most 10 seconds, then release and audit late events.
- Connected: create an agent-loss incident; never silently reassign.

## Duplicate events

`(provider_name, provider_event_id)` and `(provider_name, semantic_fingerprint)` are unique. Ingestion uses PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`; transition logic runs only if `RETURNING` yields a new row. There is no application check-then-insert race.

## Out-of-order events

Transitions are explicit, not ordinal comparisons. Allowed jumps can infer missing observations. Backward events are stored as `stale`, terminal states cannot reopen, and event insert plus transition commit in one transaction. A crash after `ANSWERED` commits both event and state, or neither.
