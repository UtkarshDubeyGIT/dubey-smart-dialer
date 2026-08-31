# Previous AI-IVR reuse assessment

The prior internship repository was reviewed with explicit permission to reuse relevant code.

## Adapted

- Python/FastAPI/SQLAlchemy/PostgreSQL modular-monolith direction.
- `TelecomProvider` boundary and normalized provider-event concept.
- Bland dispositions and vendor-shaped post-call semantics.
- Docker local operation and pytest conventions.

## Deliberately not reused

- AI prompts, STT/TTS, LLM tools, and autonomous voice behavior: this system connects borrowers to **human agents**.
- The previous multi-commit event processor: it lacked database-enforced deduplication and monotonic state protection.
- Celery/Redis: a second consistency boundary without improving the core proof. PostgreSQL intents and leases suffice here.
- Real Bland/Vapi/Twilio code and credentials: both providers are local simulators.
- Frontend portal: dashboard deferred until correctness and evidence were complete.

Safety-critical allocation, pacing, Safety Controller, event inbox, state machines, recovery, human presence, and contention tests were built for this assignment.
