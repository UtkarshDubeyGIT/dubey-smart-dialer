from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from smart_dialer.domain.states import CallState


@dataclass(frozen=True)
class PlaceCallRequest:
    idempotency_key: str
    call_intent_id: str
    phone: str
    callback_url: str


@dataclass(frozen=True)
class ProviderCallHandle:
    provider_name: str
    provider_call_id: str
    idempotency_key: str


@dataclass(frozen=True)
class NormalizedProviderEvent:
    provider_name: str
    provider_event_id: str
    call_intent_id: str
    target_state: CallState
    occurred_at: datetime
    payload: dict = field(default_factory=dict)
    semantic_fingerprint: str = ""


class TelecomProvider(Protocol):
    name: str

    def place_call(self, request: PlaceCallRequest) -> ProviderCallHandle: ...
    def lookup_by_idempotency_key(self, key: str) -> ProviderCallHandle | None: ...
    def health_check(self) -> bool: ...
    def cancel_call(self, handle: ProviderCallHandle) -> bool: ...
