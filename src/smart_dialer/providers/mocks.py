import hashlib
import random
from dataclasses import replace
from datetime import datetime, timedelta

from smart_dialer.domain.states import CallState
from smart_dialer.providers.base import (
    NormalizedProviderEvent,
    PlaceCallRequest,
    ProviderCallHandle,
)


class _IdempotentMockProvider:
    name = "mock"

    def __init__(self, *, seed: int = 1) -> None:
        self._random = random.Random(seed)
        self._calls: dict[str, ProviderCallHandle] = {}
        self.calls_created = 0
        self.healthy = True

    def place_call(self, request: PlaceCallRequest) -> ProviderCallHandle:
        existing = self._calls.get(request.idempotency_key)
        if existing is not None:
            return existing
        if not self.healthy:
            raise TimeoutError(f"{self.name} is unavailable")
        digest = hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:16]
        handle = ProviderCallHandle(self.name, f"{self.name}-{digest}", request.idempotency_key)
        self._calls[request.idempotency_key] = handle
        self.calls_created += 1
        return handle

    def lookup_by_idempotency_key(self, key: str) -> ProviderCallHandle | None:
        return self._calls.get(key)

    def health_check(self) -> bool:
        return self.healthy

    def cancel_call(self, handle: ProviderCallHandle) -> bool:
        return handle.idempotency_key in self._calls

    @staticmethod
    def _event(
        handle: ProviderCallHandle,
        call_intent_id: str,
        state: CallState,
        occurred_at: datetime,
        sequence: int,
        payload: dict | None = None,
    ) -> NormalizedProviderEvent:
        fingerprint = hashlib.sha256(
            f"{handle.provider_call_id}:{state.value}:{sequence}".encode()
        ).hexdigest()
        return NormalizedProviderEvent(
            provider_name=handle.provider_name,
            provider_event_id=f"{handle.provider_call_id}:evt:{sequence}",
            call_intent_id=call_intent_id,
            target_state=state,
            occurred_at=occurred_at,
            payload=payload or {},
            semantic_fingerprint=fingerprint,
        )


class PlivoMockProvider(_IdempotentMockProvider):
    """Fast/reliable Plivo-shaped simulator. It performs no network calls."""

    name = "plivo_mock"

    def events_for(
        self,
        handle: ProviderCallHandle,
        *,
        call_intent_id: str,
        answered: bool,
        occurred_at: datetime,
    ) -> list[NormalizedProviderEvent]:
        events = [self._event(handle, call_intent_id, CallState.RINGING, occurred_at, 1)]
        if answered:
            events.append(self._event(handle, call_intent_id, CallState.ANSWERED, occurred_at + timedelta(seconds=2), 2))
        events.append(self._event(handle, call_intent_id, CallState.COMPLETED, occurred_at + timedelta(seconds=5), 3))
        return events


class BlandMockProvider(_IdempotentMockProvider):
    """Bland-shaped simulator adapted from the user's prior IVR adapter.

    It retains realistic dispositions and post-call callback semantics, but
    deliberately makes no HTTP request and accepts no credential.
    """

    name = "bland_mock"

    def __init__(
        self,
        *,
        seed: int = 1,
        duplicate_events: bool = False,
        out_of_order_events: bool = False,
    ) -> None:
        super().__init__(seed=seed)
        self.duplicate_events = duplicate_events
        self.out_of_order_events = out_of_order_events

    def events_for(
        self,
        handle: ProviderCallHandle,
        *,
        answered: bool,
        disposition: str,
        occurred_at: datetime,
        call_intent_id: str = "intent-1",
    ) -> list[NormalizedProviderEvent]:
        common = {"disposition_tag": disposition, "call_id": handle.provider_call_id}
        events: list[NormalizedProviderEvent] = [
            self._event(handle, call_intent_id, CallState.RINGING, occurred_at, 1, common)
        ]
        if answered:
            events.append(self._event(handle, call_intent_id, CallState.ANSWERED, occurred_at + timedelta(seconds=4), 2, common))
        events.append(self._event(handle, call_intent_id, CallState.COMPLETED, occurred_at + timedelta(seconds=10), 3, common))
        if self.duplicate_events:
            original = events[-1]
            events.append(replace(original, provider_event_id=f"{original.provider_event_id}:duplicate"))
        if self.out_of_order_events:
            events.sort(key=lambda event: event.target_state is not CallState.COMPLETED)
        return events
