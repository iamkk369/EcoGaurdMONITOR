"""
End-to-end workflow test for EcoGuardAI.

Tests the complete pipeline:
  simulated sensor data
    → EmissionMonitoringAgent  (AQI + threshold check)
        → emission.violation
            → ViolationDetectionAgent  (ML/threshold confirm + Granite report)
                → violation.detected
                    → RegulatoryAlertAgent  (LOW/MEDIUM — no email/webhook)
                    → PublicHealthRiskAgent (AQI/WQI advisory)
                    → DashboardAgent        (live stats broadcast)

All test data uses LOW-severity exceedances (ratio 1.05–1.15x limit) so
that the RegulatoryAlertAgent only logs or emits dashboard events —
no email or external webhook calls are made.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.emission_agent import EmissionMonitoringAgent, calculate_aqi
from agents.violation_agent import ViolationDetectionAgent
from agents.alert_agent import RegulatoryAlertAgent
from agents.health_agent import PublicHealthRiskAgent
from agents.dashboard_agent import DashboardAgent
from core.orchestrator import AgentOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_orchestrator() -> AgentOrchestrator:
    """Create a fresh orchestrator instance (not the module-level singleton)."""
    return AgentOrchestrator()


def wire_agents(orch: AgentOrchestrator, emission, violation, alert, health, dashboard):
    """Register agents and wire up event routing — mirrors orchestrator.start()."""
    for agent in [emission, violation, alert, health, dashboard]:
        orch.register_agent(agent)

    orch.on("sensor.reading", emission.on_event)
    orch.on("emission.violation", violation.on_event)
    orch.on("violation.detected", alert.on_event)
    orch.on("violation.detected", health.on_event)
    orch.on("violation.detected", dashboard.on_event)
    orch.on("sensor.reading", dashboard.on_event)


# ---------------------------------------------------------------------------
# Shared LOW-severity sensor payloads
# SO2 = 88 µg/m³  |  limit = 80  |  ratio = 1.10  → LOW (< 1.2)
# NO external email or webhook triggered.
# ---------------------------------------------------------------------------

LOW_SEVERITY_READING = {
    "station_id": "E2E-001",
    "industry_type": "general",
    "readings": [
        {
            "pollutant_type": "SO2",
            "value": 88.0,     # 1.10× limit (LOW, no alert dispatch)
            "unit": "µg/m³",
            "timestamp": "2025-01-01T08:00:00Z",
        }
    ],
}

MEDIUM_SEVERITY_READING = {
    "station_id": "E2E-002",
    "industry_type": "general",
    "readings": [
        {
            "pollutant_type": "SO2",
            "value": 112.0,    # 1.40× limit → MEDIUM (dashboard only)
            "unit": "µg/m³",
            "timestamp": "2025-01-01T09:00:00Z",
        }
    ],
}

COMPLIANT_READING = {
    "station_id": "E2E-003",
    "industry_type": "general",
    "readings": [
        {
            "pollutant_type": "SO2",
            "value": 40.0,     # well below limit — no violation
            "unit": "µg/m³",
            "timestamp": "2025-01-01T09:30:00Z",
        }
    ],
}

MULTI_POLLUTANT_READING = {
    "station_id": "E2E-004",
    "industry_type": "general",
    "readings": [
        {"pollutant_type": "PM25",  "value": 30.0, "unit": "µg/m³"},   # compliant (limit=60)
        {"pollutant_type": "PM10",  "value": 105.0, "unit": "µg/m³"},  # LOW violation (limit=100)
        {"pollutant_type": "SO2",   "value": 40.0,  "unit": "µg/m³"},  # compliant (limit=80)
        {"pollutant_type": "BOD",   "value": 20.0,  "unit": "mg/L"},   # compliant (limit=30)
    ],
}


# ---------------------------------------------------------------------------
# Stage 1 — EmissionMonitoringAgent
# ---------------------------------------------------------------------------

class TestStage1EmissionAgent:
    """Sensor reading → AQI calculation → violation detection."""

    @pytest.fixture
    def orch(self):
        o = make_orchestrator()
        o.emit = AsyncMock()
        return o

    @pytest.fixture
    def agent(self, orch):
        a = EmissionMonitoringAgent.__new__(EmissionMonitoringAgent)
        a.orchestrator = orch
        a._limits = {
            "air": {
                "SO2":  {"limit": 80},
                "PM25": {"limit": 60},
                "PM10": {"limit": 100},
                "NOX":  {"limit": 80},
                "CO":   {"limit": 4000},
            },
            "water": {"BOD": {"limit": 30}, "COD": {"limit": 250}},
            "heavy_metals": {},
            "industry_specific": {},
        }
        return a

    @pytest.mark.asyncio
    async def test_low_severity_violation_emitted(self, agent, orch):
        """LOW-severity SO2 reading must emit exactly one emission.violation event."""
        result = await agent.run(LOW_SEVERITY_READING)

        assert result["status"] == "ok"
        assert len(result["violations"]) == 1
        v = result["violations"][0]
        assert v["pollutant_type"] == "SO2"
        assert v["measured_value"] == 88.0
        assert v["severity"] == "LOW"
        assert v["station_id"] == "E2E-001"

        orch.emit.assert_called_once()
        event_type, payload = orch.emit.call_args[0]
        assert event_type == "emission.violation"
        assert payload["pollutant_type"] == "SO2"

    @pytest.mark.asyncio
    async def test_medium_severity_violation_emitted(self, agent, orch):
        """MEDIUM-severity SO2 reading (1.4× limit) must be detected correctly."""
        result = await agent.run(MEDIUM_SEVERITY_READING)
        assert len(result["violations"]) == 1
        assert result["violations"][0]["severity"] == "MEDIUM"

    @pytest.mark.asyncio
    async def test_compliant_reading_no_violation(self, agent, orch):
        """Compliant readings must produce zero violations and no events."""
        result = await agent.run(COMPLIANT_READING)
        assert result["violations"] == []
        orch.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_multi_pollutant_only_violating_flagged(self, agent, orch):
        """Only the PM10 reading (105 > 100) should be flagged."""
        result = await agent.run(MULTI_POLLUTANT_READING)
        assert len(result["violations"]) == 1
        assert result["violations"][0]["pollutant_type"] == "PM10"

    @pytest.mark.asyncio
    async def test_aqi_computed_and_returned(self, agent, orch):
        """AQI value must be > 0 when AQI pollutants are present."""
        result = await agent.run(LOW_SEVERITY_READING)
        assert result["aqi"] > 0

    @pytest.mark.asyncio
    async def test_on_event_batch_payload(self, agent, orch):
        """on_event() must forward a batch payload to run() without error."""
        await agent.on_event(LOW_SEVERITY_READING)
        orch.emit.assert_called()

    @pytest.mark.asyncio
    async def test_on_event_single_flat_payload(self, agent, orch):
        """on_event() with a flat single-reading payload must not raise."""
        event = {"station_id": "E2E-001", "pollutant_type": "SO2", "value": 88.0, "unit": "µg/m³"}
        await agent.on_event(event)  # should not raise


# ---------------------------------------------------------------------------
# Stage 2 — ViolationDetectionAgent
# ---------------------------------------------------------------------------

class TestStage2ViolationAgent:
    """emission.violation → ML/threshold confirm → Granite report → violation.detected."""

    @pytest.fixture
    def orch(self):
        o = make_orchestrator()
        o.emit = AsyncMock()
        return o

    @pytest.fixture
    def agent(self, orch):
        with patch("agents.violation_agent.GraniteClient") as MockGranite:
            inst = MockGranite.return_value
            inst.generate_violation_report = AsyncMock(
                return_value=(
                    "Formal violation report: SO2 measured at 88 µg/m³ exceeds the NAAQ 2009 "
                    "24h standard of 80 µg/m³ at station E2E-001. Exceedance is 10.0%. "
                    "Severity classified as LOW. Corrective action recommended."
                )
            )
            return ViolationDetectionAgent(orch)

    @pytest.mark.asyncio
    async def test_violation_confirmed_report_generated(self, agent, orch):
        """A clear exceedance must be confirmed and emit violation.detected."""
        violation = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "measured_value": 88.0,
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "LOW",
            "timestamp": "2025-01-01T08:00:00Z",
        }
        result = await agent.run({"violations": [violation]})

        assert result["confirmed_violations"] == 1
        # violation.detected must have been emitted
        orch.emit.assert_called_once()
        event_type, payload = orch.emit.call_args[0]
        assert event_type == "violation.detected"
        assert "report_text" in payload
        assert len(payload["report_text"]) > 10

    @pytest.mark.asyncio
    async def test_report_text_attached_to_payload(self, agent, orch):
        """The Granite report text must be present in the emitted event payload."""
        violation = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "measured_value": 88.0,
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "LOW",
            "timestamp": "2025-01-01T08:00:00Z",
        }
        await agent.run({"violations": [violation]})
        _, payload = orch.emit.call_args[0]
        assert "SO2" in payload["report_text"] or "violation" in payload["report_text"].lower()

    @pytest.mark.asyncio
    async def test_empty_violations_nothing_emitted(self, agent, orch):
        result = await agent.run({"violations": []})
        assert result["confirmed_violations"] == 0
        orch.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_severity_reclassified(self, agent, orch):
        """ViolationAgent re-classifies severity from the measured/limit ratio."""
        violation = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "measured_value": 160.0,   # 2.0× limit → CRITICAL
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "LOW",         # incoming severity — should be overridden
            "timestamp": "2025-01-01T08:00:00Z",
        }
        await agent.run({"violations": [violation]})
        _, payload = orch.emit.call_args[0]
        assert payload["severity"] == "CRITICAL"

    @pytest.mark.asyncio
    async def test_on_event_single_violation(self, agent, orch):
        """on_event() wraps a single violation dict and calls run()."""
        event = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "measured_value": 88.0,
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "LOW",
            "timestamp": "2025-01-01T08:00:00Z",
        }
        await agent.on_event(event)
        orch.emit.assert_called_once_with(
            "violation.detected",
            pytest.approx(event, abs=1e-3),  # allow for severity reclassification
        )


# ---------------------------------------------------------------------------
# Stage 3 — RegulatoryAlertAgent (LOW + MEDIUM only — no email/webhook)
# ---------------------------------------------------------------------------

class TestStage3AlertAgent:
    """violation.detected → route alert — LOW logs, MEDIUM → dashboard event only."""

    @pytest.fixture
    def orch(self):
        o = make_orchestrator()
        o.emit = AsyncMock()
        return o

    @pytest.fixture
    def agent(self, orch):
        return RegulatoryAlertAgent(orch)

    @pytest.mark.asyncio
    async def test_low_severity_only_logged(self, agent, orch):
        """LOW violation must not call email or webhook — result status = dispatched."""
        violation = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "measured_value": 88.0,
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "LOW",
            "timestamp": "2025-01-01T08:00:00Z",
        }
        result = await agent.run(violation)
        assert result["status"] == "dispatched"
        assert result["severity"] == "LOW"
        # No orchestrator event emitted for LOW
        orch.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_medium_severity_emits_dashboard_alert(self, agent, orch):
        """MEDIUM violation must emit a dashboard.alert event (not email)."""
        violation = {
            "station_id": "E2E-002",
            "pollutant_type": "SO2",
            "measured_value": 112.0,
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "MEDIUM",
            "timestamp": "2025-01-01T09:00:00Z",
        }
        result = await agent.run(violation)
        assert result["status"] == "dispatched"
        orch.emit.assert_called_once()
        event_type, _ = orch.emit.call_args[0]
        assert event_type == "dashboard.alert"

    @pytest.mark.asyncio
    async def test_rate_limiting_suppresses_duplicate(self, agent, orch):
        """Same station+pollutant within the rate-limit window must be suppressed."""
        violation = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "measured_value": 88.0,
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "LOW",
            "timestamp": "2025-01-01T08:00:00Z",
        }
        await agent.run(violation)   # first call — recorded
        result = await agent.run(violation)  # second call — should be rate-limited
        assert result["status"] == "rate_limited"

    @pytest.mark.asyncio
    async def test_on_event_dispatches(self, agent, orch):
        """on_event() forwards directly to run()."""
        event = {
            "station_id": "E2E-RATE-NEW",
            "pollutant_type": "PM10",
            "measured_value": 105.0,
            "limit_value": 100.0,
            "unit": "µg/m³",
            "severity": "LOW",
            "timestamp": "2025-01-01T09:00:00Z",
        }
        await agent.on_event(event)
        # Should not raise; LOW → logged only


# ---------------------------------------------------------------------------
# Stage 4 — PublicHealthRiskAgent
# ---------------------------------------------------------------------------

class TestStage4HealthAgent:
    """violation.detected → AQI/WQI → affected radius → Granite advisory."""

    @pytest.fixture
    def orch(self):
        o = make_orchestrator()
        o.emit = AsyncMock()
        return o

    @pytest.fixture
    def agent(self, orch):
        with patch("agents.health_agent.GraniteClient") as MockGranite:
            inst = MockGranite.return_value
            inst.assess_health_risk = AsyncMock(
                return_value=(
                    "1. Overall risk level: SAFE\n"
                    "2. Health impacts: minor respiratory irritation for sensitive groups.\n"
                    "3. Recommended actions: vulnerable individuals remain indoors if symptoms occur."
                )
            )
            return PublicHealthRiskAgent(orch)

    @pytest.mark.asyncio
    async def test_health_assessment_complete(self, agent, orch):
        """Health assessment for a LOW SO2 violation must return all required fields."""
        context = {
            "violation": {
                "station_id": "E2E-001",
                "pollutant_type": "SO2",
                "measured_value": 88.0,
                "limit_value": 80.0,
                "unit": "µg/m³",
                "severity": "LOW",
            },
            "aqi": 55.0,
        }
        result = await agent.run(context)

        assert result["status"] == "ok"
        assert "risk_level" in result
        assert "aqi" in result
        assert "wqi" in result
        assert "affected_radius_km" in result
        assert "affected_population_estimate" in result
        assert "advisory" in result
        assert result["aqi"] == 55.0
        assert result["affected_radius_km"] > 0

    @pytest.mark.asyncio
    async def test_health_assessment_emits_event(self, agent, orch):
        """Health assessment must emit health.assessment to the orchestrator."""
        context = {
            "violation": {
                "station_id": "E2E-001",
                "pollutant_type": "SO2",
                "measured_value": 88.0,
                "limit_value": 80.0,
                "unit": "µg/m³",
                "severity": "LOW",
            },
            "aqi": 55.0,
        }
        await agent.run(context)
        orch.emit.assert_called_once()
        event_type, payload = orch.emit.call_args[0]
        assert event_type == "health.assessment"
        assert payload["risk_level"] in ("SAFE", "MODERATE", "UNHEALTHY", "HAZARDOUS")

    @pytest.mark.asyncio
    async def test_on_event_wraps_violation(self, agent, orch):
        """on_event() wraps a violation.detected event and runs the assessment."""
        event = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "measured_value": 88.0,
            "limit_value": 80.0,
            "unit": "µg/m³",
            "severity": "LOW",
            "aqi": 55.0,
        }
        await agent.on_event(event)
        orch.emit.assert_called_once()


# ---------------------------------------------------------------------------
# Stage 5 — DashboardAgent
# ---------------------------------------------------------------------------

class TestStage5DashboardAgent:
    """violation.detected / sensor.reading → in-memory state → SSE broadcast."""

    @pytest.fixture
    def orch(self):
        o = make_orchestrator()
        o.emit = AsyncMock()
        return o

    @pytest.fixture
    def agent(self, orch):
        return DashboardAgent(orch)

    @pytest.mark.asyncio
    async def test_sensor_reading_updates_aqi(self, agent, orch):
        """A sensor.reading event must update the station AQI in the snapshot."""
        event = {
            "station_id": "E2E-001",
            "pollutant_type": "SO2",
            "value": 88.0,
            "unit": "µg/m³",
            "aqi": 72.5,
        }
        await agent.on_event(event)
        snapshot = agent.get_dashboard_snapshot()
        station = next((s for s in snapshot["stations"] if s["station_id"] == "E2E-001"), None)
        assert station is not None
        assert station["aqi"] == 72.5

    @pytest.mark.asyncio
    async def test_violation_increments_active_count(self, agent, orch):
        """A violation.detected event must increment active_violations for the station."""
        event = {
            "station_id": "E2E-002",
            "pollutant_type": "SO2",
            "measured_value": 112.0,
            "limit_value": 80.0,
            "severity": "MEDIUM",
            "timestamp": "2025-01-01T09:00:00Z",
        }
        await agent.on_event(event)
        snapshot = agent.get_dashboard_snapshot()
        assert snapshot["total_active_violations"] == 1

    @pytest.mark.asyncio
    async def test_violation_added_to_recent_alerts(self, agent, orch):
        """A violation event must appear in recent_alerts."""
        event = {
            "station_id": "E2E-002",
            "pollutant_type": "NOX",
            "measured_value": 95.0,
            "limit_value": 80.0,
            "severity": "MEDIUM",
            "timestamp": "2025-01-01T09:00:00Z",
        }
        await agent.on_event(event)
        snapshot = agent.get_dashboard_snapshot()
        assert any(a["station_id"] == "E2E-002" for a in snapshot["recent_alerts"])

    @pytest.mark.asyncio
    async def test_sse_broadcast_on_violation(self, agent, orch):
        """A connected SSE client must receive a message when a violation is processed."""
        queue = agent.subscribe()
        event = {
            "station_id": "E2E-003",
            "pollutant_type": "SO2",
            "severity": "LOW",
            "timestamp": "2025-01-01T08:00:00Z",
        }
        await agent.on_event(event)
        # Both violation.new and stats.update should have been queued
        assert not queue.empty()

    @pytest.mark.asyncio
    async def test_snapshot_compliance_rate(self, agent, orch):
        """Compliance rate in snapshot must reflect reading totals."""
        # Feed one compliant reading
        await agent.on_event({"station_id": "E2E-001", "aqi": 40.0})
        snapshot = agent.get_dashboard_snapshot()
        station = next((s for s in snapshot["stations"] if s["station_id"] == "E2E-001"), None)
        assert station["compliance_rate"] == 100.0


# ---------------------------------------------------------------------------
# Stage 6 — Full pipeline integration (orchestrator-wired)
# ---------------------------------------------------------------------------

class TestFullPipelineIntegration:
    """Wire all five agents through a real orchestrator and trace event flow."""

    @pytest.mark.asyncio
    async def test_sensor_to_dashboard_low_violation(self):
        """
        Full pipeline: sensor.reading with a LOW SO2 exceedance must:
          1. Emit emission.violation
          2. Confirm as violation.detected (Granite report attached)
          3. Log only (LOW → no email/webhook)
          4. Emit health.assessment
          5. Update DashboardAgent state
        """
        orch = AgentOrchestrator()

        emitted_events: List[Dict[str, Any]] = []
        real_emit = orch.emit

        async def tracking_emit(event_type: str, payload: Dict[str, Any]):
            emitted_events.append({"type": event_type, "payload": payload})
            await real_emit(event_type, payload)

        orch.emit = tracking_emit  # type: ignore[assignment]

        # Build agents with Granite stubbed
        with patch("agents.violation_agent.GraniteClient") as MockVGranite, \
             patch("agents.health_agent.GraniteClient") as MockHGranite:

            MockVGranite.return_value.generate_violation_report = AsyncMock(
                return_value="Stub violation report: SO2 LOW exceedance at E2E-FULL-001."
            )
            MockHGranite.return_value.assess_health_risk = AsyncMock(
                return_value="Stub health advisory: risk level SAFE for SO2 LOW violation."
            )

            emission   = EmissionMonitoringAgent(orch)
            violation  = ViolationDetectionAgent(orch)
            alert      = RegulatoryAlertAgent(orch)
            health     = PublicHealthRiskAgent(orch)
            dashboard  = DashboardAgent(orch)

            # Patch internal limits on EmissionMonitoringAgent
            emission._limits = {
                "air": {"SO2": {"limit": 80}, "PM25": {"limit": 60}, "PM10": {"limit": 100}},
                "water": {"BOD": {"limit": 30}},
                "heavy_metals": {},
                "industry_specific": {},
            }

            wire_agents(orch, emission, violation, alert, health, dashboard)
            orch._running = True

            # Trigger the pipeline with a LOW SO2 violation
            await orch.emit(
                "sensor.reading",
                {
                    "station_id": "E2E-FULL-001",
                    "industry_type": "general",
                    "readings": [
                        {
                            "pollutant_type": "SO2",
                            "value": 88.0,
                            "unit": "µg/m³",
                            "timestamp": "2025-01-01T08:00:00Z",
                        }
                    ],
                },
            )

        # Verify event chain
        event_types = [e["type"] for e in emitted_events]
        assert "sensor.reading"   in event_types, "sensor.reading not in chain"
        assert "emission.violation" in event_types, "emission.violation not in chain"
        assert "violation.detected" in event_types, "violation.detected not in chain"
        assert "health.assessment"  in event_types, "health.assessment not in chain"

        # Verify violation payload integrity
        viol_event = next(e for e in emitted_events if e["type"] == "violation.detected")
        vp = viol_event["payload"]
        assert vp["station_id"] == "E2E-FULL-001"
        assert vp["pollutant_type"] == "SO2"
        assert vp["measured_value"] == 88.0
        assert vp["severity"] == "LOW"
        assert "report_text" in vp
        assert len(vp["report_text"]) > 10

        # Verify dashboard state updated
        snapshot = dashboard.get_dashboard_snapshot()
        assert snapshot["total_active_violations"] == 1
        alert_entry = snapshot["recent_alerts"][0]
        assert alert_entry["station_id"] == "E2E-FULL-001"
        assert alert_entry["severity"] == "LOW"

        # Verify health assessment event
        health_event = next(e for e in emitted_events if e["type"] == "health.assessment")
        hp = health_event["payload"]
        assert hp["status"] == "ok"
        assert hp["risk_level"] in ("SAFE", "MODERATE", "UNHEALTHY", "HAZARDOUS")
        assert hp["affected_radius_km"] > 0

    @pytest.mark.asyncio
    async def test_compliant_reading_no_downstream_events(self):
        """
        A fully compliant sensor reading must NOT trigger emission.violation,
        violation.detected, or health.assessment events.
        """
        orch = AgentOrchestrator()
        emitted_events: List[Dict[str, Any]] = []
        real_emit = orch.emit

        async def tracking_emit(event_type: str, payload: Dict[str, Any]):
            emitted_events.append({"type": event_type, "payload": payload})
            await real_emit(event_type, payload)

        orch.emit = tracking_emit  # type: ignore[assignment]

        with patch("agents.violation_agent.GraniteClient"), \
             patch("agents.health_agent.GraniteClient"):

            emission  = EmissionMonitoringAgent(orch)
            violation = ViolationDetectionAgent(orch)
            alert     = RegulatoryAlertAgent(orch)
            health    = PublicHealthRiskAgent(orch)
            dashboard = DashboardAgent(orch)

            emission._limits = {
                "air": {"SO2": {"limit": 80}},
                "water": {}, "heavy_metals": {}, "industry_specific": {},
            }

            wire_agents(orch, emission, violation, alert, health, dashboard)
            orch._running = True

            await orch.emit(
                "sensor.reading",
                {
                    "station_id": "E2E-CLEAN-001",
                    "industry_type": "general",
                    "readings": [
                        {"pollutant_type": "SO2", "value": 40.0, "unit": "µg/m³"}
                    ],
                },
            )

        event_types = [e["type"] for e in emitted_events]
        assert "emission.violation" not in event_types
        assert "violation.detected" not in event_types
        assert "health.assessment" not in event_types
        # sensor.reading and stats.update from DashboardAgent are expected
        assert "sensor.reading" in event_types

    @pytest.mark.asyncio
    async def test_multiple_stations_independent(self):
        """Events from multiple stations must not cross-contaminate each other's state."""
        orch = AgentOrchestrator()
        real_emit = orch.emit

        async def passthrough_emit(event_type: str, payload: Dict[str, Any]):
            await real_emit(event_type, payload)

        orch.emit = passthrough_emit  # type: ignore[assignment]

        with patch("agents.violation_agent.GraniteClient") as MockVG, \
             patch("agents.health_agent.GraniteClient"):

            MockVG.return_value.generate_violation_report = AsyncMock(
                return_value="Report for multi-station test."
            )

            emission  = EmissionMonitoringAgent(orch)
            violation = ViolationDetectionAgent(orch)
            alert     = RegulatoryAlertAgent(orch)
            health    = PublicHealthRiskAgent(orch)
            dashboard = DashboardAgent(orch)

            emission._limits = {
                "air": {"SO2": {"limit": 80}, "PM10": {"limit": 100}},
                "water": {}, "heavy_metals": {}, "industry_specific": {},
            }

            wire_agents(orch, emission, violation, alert, health, dashboard)
            orch._running = True

            # Station A: 1 LOW SO2 violation
            await orch.emit("sensor.reading", {
                "station_id": "E2E-MULTI-A",
                "industry_type": "general",
                "readings": [{"pollutant_type": "SO2", "value": 88.0, "unit": "µg/m³"}],
            })
            # Station B: compliant
            await orch.emit("sensor.reading", {
                "station_id": "E2E-MULTI-B",
                "industry_type": "general",
                "readings": [{"pollutant_type": "SO2", "value": 40.0, "unit": "µg/m³"}],
            })

        snapshot = dashboard.get_dashboard_snapshot()
        a_data = next((s for s in snapshot["stations"] if s["station_id"] == "E2E-MULTI-A"), None)
        b_data = next((s for s in snapshot["stations"] if s["station_id"] == "E2E-MULTI-B"), None)

        # Station A should have a violation; Station B should not
        assert a_data is not None
        assert a_data["active_violations"] == 1
        # Station B may or may not appear depending on whether compliance tracking fired
        if b_data is not None:
            assert b_data["active_violations"] == 0
