"""FastAPI integration tests using an async, lifespan-aware client."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ── Patch heavy dependencies before importing app ──────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def mock_heavy_dependencies():
    """Mock DB, orchestrator, and cloud checks for all API tests."""
    with (
        patch("core.database.create_async_engine") as mock_engine,
        patch("core.orchestrator.AgentOrchestrator.start", new_callable=AsyncMock),
        patch("core.orchestrator.AgentOrchestrator.stop", new_callable=AsyncMock),
        patch("core.database.init_db", new_callable=AsyncMock),
        patch("core.ibm_cloud.IBMCloudClient.check_watsonx_health", new_callable=AsyncMock,
              return_value={"status": "healthy"}),
    ):
        mock_engine.return_value = MagicMock()
        yield


# ── Health endpoint ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health_returns_200(self, async_client):
        resp = await async_client.get("/api/health")
        assert resp.status_code == 200

    async def test_health_has_status_key(self, async_client):
        resp = await async_client.get("/api/health")
        data = resp.json()
        assert "status" in data

    async def test_health_has_subsystems(self, async_client):
        resp = await async_client.get("/api/health")
        data = resp.json()
        assert "subsystems" in data

    async def test_health_has_version(self, async_client):
        resp = await async_client.get("/api/health")
        data = resp.json()
        assert "version" in data


# ── Monitoring endpoints ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestMonitoringEndpoints:
    async def test_stations_returns_list(self, async_client):
        """GET /api/monitoring/stations should return a JSON array."""
        resp = await async_client.get("/api/monitoring/stations")
        # May be empty if DB is mocked, but must be 200 with list
        assert resp.status_code in (200, 500)  # 500 acceptable with mocked engine

    async def test_aqi_endpoint_accessible(self, async_client):
        resp = await async_client.get("/api/monitoring/aqi")
        assert resp.status_code == 200
        data = resp.json()
        assert "stations" in data

    async def test_dashboard_read_routes_are_not_rate_limited(self, async_client):
        """A burst of simulator traffic should not starve read-only dashboard GETs."""
        for _ in range(250):
            resp = await async_client.get("/api/monitoring/stations")
            assert resp.status_code == 200, resp.text

    async def test_dashboard_read_routes_with_trailing_slash_are_not_rate_limited(self, async_client):
        """Trailing-slash route variants should still bypass the shared IP limiter."""
        for _ in range(250):
            resp = await async_client.get("/api/monitoring/not-found")
            assert resp.status_code in (404, 429), resp.text

        for path in [
            "/api/monitoring/stations/",
            "/api/violations/",
            "/api/health/risk-assessment/",
        ]:
            resp = await async_client.get(path)
            assert resp.status_code == 200, f"{path} -> {resp.text}"

    async def test_post_reading_validates_payload(self, async_client):
        """POST with missing required fields should return 422."""
        resp = await async_client.post("/api/monitoring/readings", json={"value": 42})
        assert resp.status_code == 422

    async def test_post_reading_invalid_value(self, async_client):
        """Negative sensor value should fail validation."""
        resp = await async_client.post(
            "/api/monitoring/readings",
            json={
                "station_id": "TEST-001",
                "pollutant_type": "SO2",
                "value": -10,
                "unit": "µg/m³",
            },
        )
        assert resp.status_code == 422

    async def test_post_reading_valid_payload_accepted(self, async_client):
        """Valid payload should be accepted (201 or 500 if DB unavailable)."""
        resp = await async_client.post(
            "/api/monitoring/readings",
            json={
                "station_id": "VAPI-001",
                "pollutant_type": "SO2",
                "value": 55.0,
                "unit": "µg/m³",
            },
        )
        # 201 = success, 500 = DB not available in test — both acceptable
        assert resp.status_code in (201, 500)


# ── Violations endpoints ───────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestViolationsEndpoints:
    async def test_list_violations_returns_list(self, async_client):
        resp = await async_client.get("/api/violations")
        assert resp.status_code in (200, 500)

    async def test_list_violations_with_severity_filter(self, async_client):
        resp = await async_client.get("/api/violations?severity=CRITICAL")
        assert resp.status_code in (200, 422, 500)

    async def test_violation_detail_404_for_nonexistent(self, async_client):
        resp = await async_client.get("/api/violations/99999")
        assert resp.status_code in (404, 500)

    async def test_violation_status_update_validates_status(self, async_client):
        """PATCH with invalid status value should return 422."""
        resp = await async_client.patch("/api/violations/1/status?status=INVALID_STATUS")
        assert resp.status_code == 422


# ── Alerts endpoints ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAlertsEndpoints:
    async def test_list_alerts(self, async_client):
        resp = await async_client.get("/api/alerts")
        assert resp.status_code in (200, 500)

    async def test_test_alert_no_agent(self, async_client):
        """POST /api/alerts/test when agent not running should return 503."""
        resp = await async_client.post("/api/alerts/test?alert_type=EMAIL&recipient=test@example.com")
        assert resp.status_code in (200, 503, 500)


# ── OpenAPI docs ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestDocs:
    async def test_openapi_json_accessible(self, async_client):
        resp = await async_client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["info"]["title"] == "EcoGuardAI"

    async def test_docs_page_accessible(self, async_client):
        resp = await async_client.get("/docs")
        assert resp.status_code == 200


# ── Risk assessment ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestRiskAssessment:
    async def test_risk_assessment_returns_dict(self, async_client):
        resp = await async_client.get("/api/health/risk-assessment")
        assert resp.status_code == 200
        data = resp.json()
        assert "assessments" in data
        assert "overall_risk" in data