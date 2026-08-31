# Human agent state machine

```mermaid
stateDiagram-v2
    [*] --> OFFLINE
    OFFLINE --> AVAILABLE: sign in / heartbeat
    AVAILABLE --> RESERVED: progressive allocation
    RESERVED --> DIALING: provider initiation
    DIALING --> CONNECTED: borrower answered
    CONNECTED --> WRAP_UP: call completed
    WRAP_UP --> AVAILABLE: notes complete
    AVAILABLE --> PAUSED: graceful pause
    PAUSED --> AVAILABLE: resume
    AVAILABLE --> OFFLINE: graceful sign out
    RESERVED --> AVAILABLE: setup failed
    DIALING --> AVAILABLE: no answer / cancel confirmed
```

Human workstations heartbeat every 5 seconds. Graceful `PAUSED`/`OFFLINE` is immediate. Silent crash/network loss is detected at 15 seconds. A ringing call first receives a cancellation request and a 10-second reconcile lease; ownership-checked release occurs on confirmation or expiry. Connected disappearance creates an incident and never silently reassigns the borrower.
