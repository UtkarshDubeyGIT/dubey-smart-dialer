from smart_dialer.providers.mocks import BlandMockProvider, PlivoMockProvider


PLIVO = PlivoMockProvider(seed=2026)
BLAND = BlandMockProvider(seed=2027, duplicate_events=True, out_of_order_events=True)


def get_provider(name: str):
    if name == "plivo_mock":
        return PLIVO
    if name == "bland_mock":
        return BLAND
    raise ValueError(f"unknown provider: {name}")


def get_alternate(name: str):
    return BLAND if name == "plivo_mock" else PLIVO
