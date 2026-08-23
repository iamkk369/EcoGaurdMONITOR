"""Unit tests for individual EcoGuardAI agents."""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.emission_agent import EmissionMonitoringAgent, calculate_aqi
from agents.violation_agent import ViolationDetectionAgent
from agents.dashboard_agent import DashboardAgent
from agents.health_agent import classify_aqi, calculate_wqi, estimate_affected_radius


LIMITS = {
    "air": {"SO2": {"limit": 80}, "PM25": {"limit": 60}, "PM10": {"limit": 100}},
    "water": {"BOD": {"limit": 30}},
    "heavy_metals": {},
    "industry_specific": {},
}


def make_emission():
    o = MagicMock()
    o.emit = AsyncMock()
    a = EmissionMonitoringAgent.__new__(EmissionMonitoringAgent)
    a.orchestrator = o
    a._limits = LIMITS
    return a, o


# ── AQI calculation ───────────────────────────────────────────────────────

class TestAQICalculation:
    def test_aqi_zero_for_empty_readings(self):
        assert calculate_aqi([], LIMITS) == 0.0

    def test_overall_aqi_uses_max_sub_index(self):
        readings = [
            {"pollutant_type": "SO2", "value": 88.0},   # 110
            {"pollutant_type": "PM10", "value": 105.0},  # 105
        ]
        assert calculate_aqi(readings, LIMITS) == 110.0

    def test_pm25_sub_index_good(self):
        assert calculate_aqi([{"pollutant_type": "PM25", "value": 30}], LIMITS) <= 50

    def test_pm25_sub_index_moderate(self):
        v = calculate_aqi([{"pollutant_type": "PM25", "value": 54}], LIMITS)
        assert 50 < v <= 100

    def test_pm25_sub_index_very_poor(self):
        v = calculate_aqi([{"pollutant_type": "PM25", "value": 120}], LIMITS)
        assert v >= 200

    def test_unknown_pollutant_ignored(self):
        assert calculate_aqi([{"pollutant_type": "XYZ", "value": 999}], LIMITS) == 0.0


# ── EmissionMonitoringAgent ───────────────────────────────────────────────

class TestEmissionMonitoringAgent:
    @pytest.mark.asyncio
    async def test_empty_readings(self):
        agent, orch = make_emission()
        result = await agent.run({"station_id": "S1", "readings": []})
        assert result["status"] == "ok"
        assert result["violations"] == []
        orch.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_violation_within_limit(self):
        agent, orch = make_emission()
        result = await agent.run(
            {"station_id": "S1", "readings": [{"pollutant_type": "SO2", "value": 40.0}]}
        )
        assert result["violations"] == []
        orch.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_violation_above_limit(self):
        agent, orch = make_emission()
        result = await agent.run(
            {"station_id": "S1", "readings": [{"pollutant_type": "SO2", "value": 88.0}]}
        )
        assert len(result["violations"]) == 1
        v = result["violations"][0]
        assert v["measured_value"] == 88.0
        assert v["severity"] == "LOW"
        orch.emit.assert_called_once()
        assert orch.emit.call_args[0][0] == "emission.violation"

    @pytest.mark.asyncio
    async def test_severity_low(self):
        agent, _ = make_emission()
        result = await agent.run(
            {"station_id": "S1", "readings": [{"pollutant_type": "SO2", "value": 88.0}]}
        )
        assert result["violations"][0]["severity"] == "LOW"

    @pytest.mark.asyncio
    async def test_severity_critical(self):
        agent, _ = make_emission()
        result = await agent.run(
            {"station_id": "S1", "readings": [{"pollutant_type": "SO2", "value": 200.0}]}
        )
        assert result["violations"][0]["severity"] == "CRITICAL"

    @pytest.mark.asyncio
    async def test_multiple_readings_batch(self):
        agent, orch = make_emission()
        result = await agent.run({
            "station_id": "S1",
            "readings": [
                {"pollutant_type": "PM10", "value": 105.0},
                {"pollutant_type": "SO2", "value": 40.0},
            ],
        })
        assert len(result["violations"]) == 1
        assert result["violations"][0]["pollutant_type"] == "PM10"

    @pytest.mark.asyncio
    async def test_on_event_single_reading(self):
        agent, orch = make_emission()
        await agent.on_event(
            {"station_id": "S1", "pollutant_type": "SO2", "value": 96.0}
        )
        orch.emit.assert_called_once()

# ── ViolationDetectionAgent ───────────────────────────────────────────────

class TestViolationDetectionAgent:
    def make_agent(self):
        orch = MagicMock()
        orch.emit = AsyncMock()
        with patch("agents.violation_agent.GraniteClient") as mock_g:
            inst = mock_g.return_value
            inst.generate_violation_report = AsyncMock(return_value="Formal violation report text.")
            agent = ViolationDetectionAgent(orch)
        return agent, orch

    @pytest.mark.asyncio
    async def test_empty_violations(self):
        agent, orch = self.make_agent()
        result = await agent.run({"violations": []})
        assert result["confirmed_violations"] == 0
        orch.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_violation_confirmed_and_emitted(self):
        agent, orch = self.make_agent()
        violation = {
            "station_id": "S1", "pollutant_type": "SO2",
            "measured_value": 88.0, "limit_value": 80.0,
        }
        result = await agent.run({"violations": [violation]})
        assert result["confirmed_violations"] == 1
        orch.emit.assert_called_once()
        event_type, payload = orch.emit.call_args[0]
        assert event_type == "violation.detected"
        assert "report_text" in payload

    def test_severity_thresholds(self):
        agent, _ = self.make_agent()
        assert agent._classify_severity(88.0, 80.0) == "LOW"      # 1.10
        assert agent._classify_severity(110.0, 80.0) == "MEDIUM"  # 1.375
        assert agent._classify_severity(130.0, 80.0) == "HIGH"    # 1.625
        assert agent._classify_severity(200.0, 80.0) == "CRITICAL"  # 2.5


# ── PublicHealthRisk helpers ──────────────────────────────────────────────

class TestPublicHealthRiskAgent:
    def test_classify_aqi_categories(self):
        assert classify_aqi(30)["level"] == "SAFE"
        assert classify_aqi(150)["level"] == "MODERATE"
        assert classify_aqi(250)["level"] == "UNHEALTHY"
        assert classify_aqi(350)["level"] == "HAZARDOUS"

    def test_low_aqi_returns_safe(self):
        assert classify_aqi(25)["level"] == "SAFE"

    def test_high_aqi_returns_unhealthy_or_hazardous(self):
        assert classify_aqi(250)["level"] in ("UNHEALTHY", "HAZARDOUS")

    def test_wqi_calculation(self):
        assert calculate_wqi({}) == 0.0
        good = calculate_wqi({"PH": 7.0})
        bad = calculate_wqi({"BOD": 60.0})
        assert good < bad
        assert bad > 100

    def test_affected_radius_capped_at_25km(self):
        assert estimate_affected_radius(800000.0, 80.0) <= 25.0

    def test_affected_radius_increases_with_exceedance(self):
        r_low = estimate_affected_radius(88.0, 80.0)
        r_high = estimate_affected_radius(160.0, 80.0)
        assert r_high >= r_low


# ── DashboardAgent ────────────────────────────────────────────────────────

class TestDashboardAgent:
    def make_agent(self):
        return DashboardAgent(MagicMock())

    def test_initial_snapshot_empty(self):
        snap = self.make_agent().get_dashboard_snapshot()
        assert snap["stations"] == []
        assert snap["total_active_violations"] == 0
        assert snap["station_count"] == 0

    def test_aqi_category_labels(self):
        from agents.dashboard_agent import _aqi_category
        assert _aqi_category(30) == "Good"
        assert _aqi_category(80) == "Satisfactory"
        assert _aqi_category(150) == "Moderate"
        assert _aqi_category(250) == "Poor"
        assert _aqi_category(350) == "Very Poor"
        assert _aqi_category(450) == "Severe"

    @pytest.mark.asyncio
    async def test_on_event_violation_increments_count(self):
        agent = self.make_agent()
        await agent.on_event({
            "severity": "HIGH", "station_id": "S1",
            "pollutant_type": "SO2", "timestamp": "2025-01-01T08:00:00Z",
        })
        snap = agent.get_dashboard_snapshot()
        assert snap["total_active_violations"] == 1
        assert len(snap["recent_alerts"]) == 1

    def test_subscribe_returns_queue(self):
        agent = self.make_agent()
        q = agent.subscribe()
        assert isinstance(q, asyncio.Queue)
        assert q in agent._sse_queues

    def test_unsubscribe_removes_queue(self):
        agent = self.make_agent()
        q = agent.subscribe()
        agent.unsubscribe(q)
        assert q not in agent._sse_queues
