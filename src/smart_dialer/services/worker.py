from smart_dialer.db.models import CallIntent
from smart_dialer.domain.states import CallState
from smart_dialer.providers.base import PlaceCallRequest, TelecomProvider


def initiate_intent_with_reconciliation(
    intent: CallIntent,
    *,
    phone: str,
    provider: TelecomProvider,
    alternate: TelecomProvider | None = None,
) -> str:
    """Initiate with provider-local idempotency and ambiguous-failure reconciliation.

    A lease may double-claim work. The provider idempotency lookup is the
    correctness guarantee; the lease is only a liveness bound.
    """
    request = PlaceCallRequest(
        idempotency_key=intent.provider_idempotency_key,
        call_intent_id=intent.id,
        phone=phone,
        callback_url="http://api:8000/v1/provider-events",
    )
    try:
        existing = provider.lookup_by_idempotency_key(request.idempotency_key)
    except Exception:
        intent.state = CallState.AMBIGUOUS
        intent.manual_review_reason = "pre-initiation provider reconciliation was inconclusive"
        return "ambiguous"
    if existing is not None:
        intent.provider_call_id = existing.provider_call_id
        intent.state = CallState.INITIATED
        return "reconciled"
    try:
        handle = provider.place_call(request)
    except TimeoutError:
        try:
            handle = provider.lookup_by_idempotency_key(request.idempotency_key)
        except Exception:
            intent.state = CallState.AMBIGUOUS
            intent.manual_review_reason = "provider placement and reconciliation were both inconclusive"
            return "ambiguous"
        if handle is not None:
            intent.provider_call_id = handle.provider_call_id
            intent.state = CallState.INITIATED
            return "reconciled"
        if alternate is None or not alternate.health_check():
            intent.state = CallState.FAILED
            intent.manual_review_reason = "original provider confirmed no call; no healthy alternate"
            return "failed"
        # Idempotency scope is provider-local, so failover receives a new key.
        alternate_key = f"{request.idempotency_key.rsplit(':', 1)[0]}:{alternate.name}"
        alternate_request = PlaceCallRequest(
            idempotency_key=alternate_key,
            call_intent_id=intent.id,
            phone=phone,
            callback_url=request.callback_url,
        )
        handle = alternate.place_call(alternate_request)
        intent.provider_name = alternate.name
        intent.provider_idempotency_key = alternate_key
        intent.provider_call_id = handle.provider_call_id
        intent.state = CallState.INITIATED
        return "failed-over"
    intent.provider_call_id = handle.provider_call_id
    intent.state = CallState.INITIATED
    return "initiated"
