# Call state machine

```mermaid
stateDiagram-v2
    [*] --> QUEUED
    QUEUED --> RESERVED
    RESERVED --> INITIATED
    INITIATED --> RINGING
    RINGING --> ANSWERED
    ANSWERED --> CONNECTED
    CONNECTED --> COMPLETED
    RESERVED --> CANCELLED
    INITIATED --> CANCELLED
    RINGING --> CANCELLED
    RESERVED --> FAILED
    INITIATED --> FAILED
    RINGING --> FAILED
    INITIATED --> AMBIGUOUS
    RINGING --> AMBIGUOUS
    COMPLETED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
    AMBIGUOUS --> [*]
```

The code explicitly enumerates valid jumps. `INITIATED -> COMPLETED` is accepted but marks the answer as **inferred**. Only directly observed `ANSWERED` contributes to pacing statistics. Terminal states absorb later events; conflicting terminal events remain audited without replacing the first accepted terminal result.
