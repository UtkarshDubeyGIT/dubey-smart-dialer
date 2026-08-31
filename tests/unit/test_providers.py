from datetime import UTC, datetime

from smart_dialer.providers.base import PlaceCallRequest
from smart_dialer.providers.mocks import BlandMockProvider, PlivoMockProvider


def request(key: str = "intent:1:plivo_mock") -> PlaceCallRequest:
    return PlaceCallRequest(
        idempotency_key=key,
        call_intent_id="intent-1",
        phone="+919999999999",
        callback_url="http://api/v1/provider-events",
    )


def test_plivo_mock_deduplicates_call_creation_by_idempotency_key() -> None:
    provider = PlivoMockProvider(seed=7)

    first = provider.place_call(request())
    second = provider.place_call(request())

    assert first == second
    assert provider.calls_created == 1
    assert provider.lookup_by_idempotency_key(request().idempotency_key) == first


def test_bland_mock_matches_vendor_shaped_disposition_events() -> None:
    provider = BlandMockProvider(seed=11, duplicate_events=True, out_of_order_events=True)
    handle = provider.place_call(request("intent:1:bland_mock"))

    events = provider.events_for(
        handle,
        answered=True,
        disposition="CALL_BACK_SCHEDULED",
        occurred_at=datetime(2026, 8, 31, tzinfo=UTC),
    )

    assert events[0].target_state.value == "completed"
    assert any(event.target_state.value == "answered" for event in events)
    assert len({event.semantic_fingerprint for event in events}) < len(events)
    assert any(event.payload.get("disposition_tag") == "CALL_BACK_SCHEDULED" for event in events)


def test_bland_mock_also_deduplicates_provider_requests() -> None:
    provider = BlandMockProvider(seed=5)
    req = request("intent:9:bland_mock")

    assert provider.place_call(req) == provider.place_call(req)
    assert provider.calls_created == 1
