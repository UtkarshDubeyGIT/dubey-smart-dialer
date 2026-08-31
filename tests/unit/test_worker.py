from datetime import UTC, datetime

from smart_dialer.db.models import CallIntent, IntentMode
from smart_dialer.domain.states import CallState
from smart_dialer.providers.base import PlaceCallRequest
from smart_dialer.providers.mocks import BlandMockProvider, PlivoMockProvider
from smart_dialer.services.worker import initiate_intent_with_reconciliation


def intent(provider: str = "plivo_mock") -> CallIntent:
    return CallIntent(
        id="intent-1", campaign_id="campaign", borrower_id="borrower", agent_id="agent",
        mode=IntentMode.PROGRESSIVE, state=CallState.RESERVED, provider_name=provider,
        provider_idempotency_key=f"intent:intent-1:{provider}", lease_owner="worker",
        lease_expires_at=datetime.now(UTC),
    )


def test_worker_attaches_provider_call_and_marks_initiated() -> None:
    row = intent()
    outcome = initiate_intent_with_reconciliation(
        row, phone="+919999999999", provider=PlivoMockProvider(seed=1)
    )

    assert outcome == "initiated"
    assert row.state is CallState.INITIATED
    assert row.provider_call_id is not None


class PlaceThenTimeout(PlivoMockProvider):
    def place_call(self, request: PlaceCallRequest):
        super().place_call(request)
        raise TimeoutError("response lost after provider accepted call")


def test_ambiguous_timeout_reconciles_original_provider_before_any_failover() -> None:
    row = intent()
    original = PlaceThenTimeout(seed=1)
    alternate = BlandMockProvider(seed=2)

    outcome = initiate_intent_with_reconciliation(
        row, phone="+919999999999", provider=original, alternate=alternate
    )

    assert outcome == "reconciled"
    assert row.provider_call_id is not None
    assert alternate.calls_created == 0


class InconclusiveLookup(PlivoMockProvider):
    def place_call(self, request: PlaceCallRequest):
        raise TimeoutError("unknown")

    def lookup_by_idempotency_key(self, key: str):
        raise TimeoutError("lookup also unavailable")


def test_inconclusive_reconciliation_never_fails_over_and_marks_ambiguous() -> None:
    row = intent()
    alternate = BlandMockProvider(seed=2)

    outcome = initiate_intent_with_reconciliation(
        row, phone="+919999999999", provider=InconclusiveLookup(seed=1), alternate=alternate
    )

    assert outcome == "ambiguous"
    assert row.state is CallState.AMBIGUOUS
    assert row.manual_review_reason
    assert alternate.calls_created == 0


class ConfirmedNoCall(PlivoMockProvider):
    def place_call(self, request: PlaceCallRequest):
        raise TimeoutError("failed before placement")


def test_confirmed_absence_allows_alternate_with_provider_specific_key() -> None:
    row = intent()
    alternate = BlandMockProvider(seed=2)

    outcome = initiate_intent_with_reconciliation(
        row, phone="+919999999999", provider=ConfirmedNoCall(seed=1), alternate=alternate
    )

    assert outcome == "failed-over"
    assert row.provider_name == "bland_mock"
    assert row.provider_idempotency_key.endswith(":bland_mock")
    assert alternate.calls_created == 1
