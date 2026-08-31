from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from smart_dialer.api import create_app

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(session_factory) -> Iterator[TestClient]:
    app = create_app(session_factory=session_factory)
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_end_to_end_progressive_tick(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    campaign = client.post("/v1/campaigns", json={"name": "Demo", "mode": "progressive"}).json()
    campaign_id = campaign["id"]
    agent = client.post("/v1/agents", json={
        "campaign_id": campaign_id, "name": "Reviewer", "language": "en-IN",
    }).json()
    client.post(f"/v1/agents/{agent['id']}/heartbeat")
    client.post("/v1/borrowers", json={
        "campaign_id": campaign_id, "external_id": "B-1",
        "phone": "+919999999999", "language": "en-IN",
    })

    tick = client.post(f"/v1/campaigns/{campaign_id}/pacing-tick", json={}).json()

    assert tick["approved_calls"] == tick["created_intents"] == 1
    assert client.get("/v1/call-intents").json()[0]["agent_id"] == agent["id"]
    assert len(client.get("/v1/safety-decisions").json()) == 1


def test_agent_graceful_state_and_operational_lists(client: TestClient) -> None:
    campaign_id = client.post("/v1/campaigns", json={"name": "Ops", "mode": "progressive"}).json()["id"]
    agent_id = client.post("/v1/agents", json={
        "campaign_id": campaign_id, "name": "Human", "language": "en-IN",
    }).json()["id"]
    response = client.post(f"/v1/agents/{agent_id}/state", json={"state": "paused"})

    assert response.status_code == 200
    assert response.json()["state"] == "paused"
    assert client.get("/v1/incidents").status_code == 200
    assert client.get("/v1/manual-review").status_code == 200


def test_pacing_api_rejects_caller_supplied_provider_health(client: TestClient) -> None:
    campaign_id = client.post(
        "/v1/campaigns", json={"name": "Runtime health", "mode": "predictive"}
    ).json()["id"]

    response = client.post(
        f"/v1/campaigns/{campaign_id}/pacing-tick",
        json={"provider_healthy": False},
    )

    assert response.status_code == 422


def test_pacing_api_rejects_caller_supplied_answer_statistics(client: TestClient) -> None:
    campaign_id = client.post(
        "/v1/campaigns", json={"name": "Database statistics", "mode": "predictive"}
    ).json()["id"]

    response = client.post(
        f"/v1/campaigns/{campaign_id}/pacing-tick",
        json={"observed_answers": 30, "observed_attempts": 100},
    )

    assert response.status_code == 422
