"""Multi-agent orchestrator — event bus, lifecycle management."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine, Dict, List, Optional

from loguru import logger

from core.data_ingestion import MQTTIngestionClient


EventHandler = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


class AgentOrchestrator:
    """Central event bus that wires all EcoGuardAI agents together.

    Event flow:
        sensor.reading
            → EmissionMonitoringAgent  (AQI, threshold check)
                → emission.violation
                    → ViolationDetectionAgent  (ML severity, Granite report)
                        → violation.detected
                            ├─ RegulatoryAlertAgent  (email / webhook)
                            ├─ PublicHealthRiskAgent  (AQI/WQI advisory)
                            └─ DashboardAgent  (live stats broadcast)
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._agents: List[Any] = []
        self._mqtt: Optional[MQTTIngestionClient] = None
        self._running = False

    # ── Registration ──────────────────────────────────────────────────────────

    def register_agent(self, agent: Any) -> None:
        """Add an agent instance to the orchestrator."""
        self._agents.append(agent)
        logger.info("Registered agent: {}", agent.name)

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe *handler* to *event_type*."""
        self._handlers[event_type].append(handler)

    # ── Emission ──────────────────────────────────────────────────────────────

    async def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Publish an event to all registered handlers.

        Handlers run concurrently; individual failures are logged but do not
        prevent other handlers from executing.
        """
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            logger.debug("No handlers for event: {}", event_type)
            return

        logger.debug("Emitting {} -> {} handler(s)", event_type, len(handlers))
        results = await asyncio.gather(
            *[h(payload) for h in handlers], return_exceptions=True
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error("Handler[{}] for {} raised: {}", i, event_type, result)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start all agents and the MQTT listener."""
        if self._running:
            logger.warning("Orchestrator already running.")
            return

        logger.info("Starting AgentOrchestrator with {} agent(s)", len(self._agents))

        # Import here to avoid circular imports at module level
        from agents.emission_agent import EmissionMonitoringAgent
        from agents.violation_agent import ViolationDetectionAgent
        from agents.alert_agent import RegulatoryAlertAgent
        from agents.health_agent import PublicHealthRiskAgent
        from agents.dashboard_agent import DashboardAgent

        # Instantiate agents
        emission = EmissionMonitoringAgent(self)
        violation = ViolationDetectionAgent(self)
        alert = RegulatoryAlertAgent(self)
        health = PublicHealthRiskAgent(self)
        dashboard = DashboardAgent(self)

        for agent in [emission, violation, alert, health, dashboard]:
            self.register_agent(agent)

        # Wire event routing
        self.on("sensor.reading", emission.on_event)
        self.on("emission.violation", violation.on_event)
        # Step 1 (persistence): fail-safe DB persistence of violations.
        # Runs alongside (never replacing) the existing handlers below.
        from services.persistence import handle_violation_detected
        self.on("violation.detected", handle_violation_detected)
        self.on("violation.detected", alert.on_event)
        self.on("violation.detected", health.on_event)
        self.on("violation.detected", dashboard.on_event)
        self.on("sensor.reading", dashboard.on_event)

        # Start MQTT
        self._mqtt = MQTTIngestionClient()
        self._mqtt.register_callback(self._handle_mqtt_payload)
        loop = asyncio.get_event_loop()
        self._mqtt.start(loop)

        self._running = True
        logger.info("AgentOrchestrator started.")

    async def stop(self) -> None:
        """Gracefully shut down all agents and MQTT."""
        if not self._running:
            return

        logger.info("Stopping AgentOrchestrator…")
        if self._mqtt:
            self._mqtt.stop()

        for agent in self._agents:
            if hasattr(agent, "shutdown"):
                try:
                    await agent.shutdown()
                except Exception as exc:
                    logger.error("Error shutting down agent {}: {}", agent.name, exc)

        self._running = False
        logger.info("AgentOrchestrator stopped.")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _handle_mqtt_payload(self, payload: Dict[str, Any]) -> None:
        """Route a raw MQTT message into the sensor.reading event."""
        await self.emit("sensor.reading", payload)

    @property
    def is_running(self) -> bool:
        return self._running


# Singleton — imported by api/main.py and agents
orchestrator = AgentOrchestrator()