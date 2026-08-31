# Scale and load-test analysis

Run `make load-test`. The deterministic test uses seed `2026`, ephemeral PostgreSQL 16, real concurrent connections, shared-start workers, and the production allocator. It emits one comparison table to `reports/load-test.json` and `.csv` for 100, 1,000, and 10,000 agents.

Metrics include throughput, p50/p95/p99 latency, skip/retry/deadlock counts, duplicate assignments, maximum checked-out connections, pool saturation, and 40% sudden-drop release timing.

## What breaks first?

The first likely bottleneck is the PostgreSQL connection budget, not waiting on allocation rows. `SKIP LOCKED` prevents workers blocking behind claimed candidates, but every API/worker needs a connection. Unbounded concurrency can exhaust the application pool and PostgreSQL `max_connections` before row-lock waits dominate.

Response, in order:

1. Bound worker concurrency and expose pool checkout wait/saturation.
2. Add PgBouncer transaction pooling (optional Compose `pooler` profile supplied).
3. Batch pacing decisions and claims rather than one round trip per intent.
4. Keep narrow covering indexes; partition large event/call tables by campaign/time.
5. Shard campaign ownership only after one PostgreSQL writer is measurably saturated.

Later bottlenecks are provider limits, event write amplification, and repeated binomial calculations. Cache pure statistical results only after profiling; never cache allocation truth.

The PgBouncer profile is optional so reviewer startup stays simple. A rigorous direct-versus-pooler comparison requires the same harness through port `6432`; this is identified as follow-up instead of presenting invented numbers.
