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
    intent = client.get("/v1/call-intents").json()[0]
    decisions = client.get("/v1/safety-decisions").json()
    assert intent["agent_id"] == agent["id"]
    assert intent["safety_decision_id"] == decisions[0]["id"]
    assert decisions[0]["effective_mode"] == "progressive"


def test_root_opens_branded_dashboard_with_helpful_empty_state(
    client: TestClient,
) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert dashboard.headers["content-type"].startswith("text/html")
    assert "CredResolve" in dashboard.text
    assert "Safety pulse" in dashboard.text
    assert "No campaigns yet" in dashboard.text


def test_dashboard_renders_live_campaign_agent_and_safety_data(
    client: TestClient,
) -> None:
    campaign = client.post(
        "/v1/campaigns", json={"name": "North India Resolution", "mode": "progressive"}
    ).json()
    agent = client.post("/v1/agents", json={
        "campaign_id": campaign["id"], "name": "Aarav Mehta", "language": "en-IN",
    }).json()
    client.post(f"/v1/agents/{agent['id']}/heartbeat")
    client.post("/v1/borrowers", json={
        "campaign_id": campaign["id"], "external_id": "CR-1001",
        "phone": "+919999999998", "language": "en-IN",
    })
    client.post(f"/v1/campaigns/{campaign['id']}/pacing-tick", json={})

    dashboard = client.get("/dashboard")

    assert dashboard.status_code == 200
    assert "North India Resolution" in dashboard.text
    assert "Aarav Mehta" in dashboard.text
    assert "Progressive" in dashboard.text
    assert "Approved" in dashboard.text
    assert "Plivo Mock" in dashboard.text


def test_dashboard_styles_are_packaged_with_the_application(
    client: TestClient,
) -> None:
    response = client.get("/static/dashboard.css")
    dashboard = client.get("/dashboard")

    assert response.status_code == 200
    assert "--orange: #ff641e" in response.text
    assert ".identity-orb" in response.text
    assert "radial-gradient" in response.text
    assert "Live pacing curve" in dashboard.text
    assert 'href="/static/dashboard.css"' in dashboard.text


def test_public_demo_mode_keeps_dashboard_visible_but_blocks_mutations(
    session_factory,
) -> None:
    app = create_app(session_factory=session_factory, read_only=True)
    with TestClient(app) as read_only_client:
        dashboard = read_only_client.get("/dashboard")
        mutation = read_only_client.post(
            "/v1/campaigns", json={"name": "Must not be created"}
        )

    assert dashboard.status_code == 200
    assert mutation.status_code == 403
    assert mutation.json() == {
        "detail": "Public demo is read-only. Use the local CLI or API to make changes."
    }


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


def test_pacing_api_rejects_caller_supplied_rapid_agent_drop(client: TestClient) -> None:
    campaign_id = client.post(
        "/v1/campaigns", json={"name": "Server presence", "mode": "predictive"}
    ).json()["id"]

    response = client.post(
        f"/v1/campaigns/{campaign_id}/pacing-tick",
        json={"rapid_agent_drop": True},
    )

    assert response.status_code == 422
