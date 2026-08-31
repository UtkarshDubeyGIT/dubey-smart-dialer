from collections.abc import Iterator
from pathlib import Path

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
    interaction = client.get("/static/dashboard.js")
    dashboard = client.get("/dashboard")

    assert response.status_code == 200
    assert interaction.status_code == 200
    assert "--accent: #d95f28" in response.text
    assert "--canvas: #f7f6f3" in response.text
    assert ".decision-lab" in response.text
    assert "gradient" not in response.text
    assert "color-scheme: light" in response.text
    assert "Graduate AI/ML systems assignment" in dashboard.text
    assert "Watch the dialer decide" in dashboard.text
    assert 'data-decision-lab' in dashboard.text
    assert 'href="/static/dashboard.css?v=3"' in dashboard.text
    assert 'src="/static/dashboard.js?v=3"' in dashboard.text


def test_submission_is_attributed_and_readme_exposes_live_frontend(
    client: TestClient,
) -> None:
    dashboard = client.get("/dashboard")
    readme = Path("README.md").read_text()

    assert "Designed and engineered by Utkarsh Dubey" in dashboard.text
    assert "mailto:utkarsh.dubey.ug23@nsut.ac.in" in dashboard.text
    assert "https://github.com/UtkarshDubeyGIT" in dashboard.text
    assert "https://dialer-dashboard.dubey.page/dashboard" in readme
    assert "docs/dashboard-preview.jpg" in readme
    assert "Designed and engineered by **Utkarsh Dubey**" in readme


def test_decision_lab_runs_the_production_pacing_and_safety_path(
    client: TestClient,
) -> None:
    response = client.get(
        "/v1/demo/pacing-decision",
        params={
            "available_agents": 10,
            "ringing_calls": 2,
            "observed_answers": 30,
            "observed_attempts": 100,
            "risk_tolerance": 0.005,
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["engine"] == "production"
    assert result["proposal"]["requested_calls"] > 10
    assert 10 < result["receipt"]["approved_calls"] <= result["proposal"]["requested_calls"]
    assert result["receipt"]["effective_mode"] == "predictive"
    assert result["receipt"]["answer_rate_upper_bound"] > 0.30
    assert result["receipt"]["overload_probability"] <= 0.005


def test_decision_lab_exposes_live_progressive_fallbacks(
    session_factory,
) -> None:
    app = create_app(session_factory=session_factory, read_only=True)
    with TestClient(app) as read_only_client:
        zero_risk = read_only_client.get(
            "/v1/demo/pacing-decision",
            params={
                "available_agents": 7,
                "observed_answers": 30,
                "observed_attempts": 100,
                "risk_tolerance": 0,
            },
        )
        rapid_drop = read_only_client.get(
            "/v1/demo/pacing-decision",
            params={
                "available_agents": 7,
                "observed_answers": 30,
                "observed_attempts": 100,
                "rapid_agent_drop": True,
            },
        )

    assert zero_risk.status_code == 200
    assert zero_risk.json()["receipt"]["effective_mode"] == "progressive"
    assert zero_risk.json()["receipt"]["approved_calls"] == 7
    assert "zero risk policy" in zero_risk.json()["receipt"]["reasons"]
    assert rapid_drop.status_code == 200
    assert rapid_drop.json()["receipt"]["effective_mode"] == "progressive"
    assert "rapid agent availability drop" in rapid_drop.json()["receipt"]["reasons"]


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
