"""Industrial Pollution Dashboard Agent — aggregates and broadcasts live stats."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from agents.base_agent import BaseAgent


class DashboardAgent(BaseAgent):
    """Aggregates real-time pollution statistics and broadcasts them via SSE.

    Maintains in-memory state:
    - ``_station_aqi``:       latest AQI per station
    - ``_active_violations``: count of open violations per station
    - ``_recent_alerts``:     ring-buffer of last 50 alerts
    - ``_compliance_totals``: (total_readings, compliant_readings) per station

    The ``sse_queue`` is an asyncio.Queue consumed by the FastAPI ``/api/stream``
    SSE endpoint to push updates to connected browsers.
    """

    name = "DashboardAgent"
    description = "Aggregates live stats and broadcasts via Server-Sent Events."

    MAX_ALERT_HISTORY = 50

    def __init__(self, orchestrator: Any) -> None:
        super().__init__(orchestrator)
        self._station_aqi: Dict[str, float] = {}
        self._active_violations: Dict[str, int] = defaultdict(int)
        self._recent_alerts: List[Dict[str, Any]] = []
        self._compliance_totals: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
        # Multiple SSE clients can subscribe
        self._sse_queues: List[asyncio.Queue] = []

    # ── SSE subscription ──────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        """Register a new SSE client and return its dedicated queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._sse_queues.append(q)
        self.log(f"SSE client subscribed. Total clients: {len(self._sse_queues)}")
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Remove an SSE client's queue when the connection closes."""
        try:
            self._sse_queues.remove(q)
            self.log(f"SSE client unsubscribed. Total clients: {len(self._sse_queues)}")
        except ValueError:
            pass

    async def _broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """Push a JSON-serialised event to all connected SSE clients."""
        message = json.dumps({"type": event_type, "data": data, "ts": datetime.now(timezone.utc).isoformat()})
        dead_queues = []
        for q in self._sse_queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("SSE queue full; dropping message for a slow client.")
            except Exception:
                dead_queues.append(q)

        for q in dead_queues:
            self.unsubscribe(q)

    # ── State helpers ─────────────────────────────────────────────────────────

    def _update_aqi(self, station_id: str, aqi: float) -> None:
        self._station_aqi[station_id] = round(aqi, 1)

    def _record_reading(self, station_id: str, is_compliant: bool) -> None:
        totals = self._compliance_totals[station_id]
        totals[0] += 1  # total
        if is_compliant:
            totals[1] += 1  # compliant

    def _add_alert(self, alert: Dict[str, Any]) -> None:
        self._recent_alerts.append(alert)
        if len(self._recent_alerts) > self.MAX_ALERT_HISTORY:
            self._recent_alerts.pop(0)

    def get_dashboard_snapshot(self) -> Dict[str, Any]:
        """Return a complete snapshot of the current dashboard state."""
        stations = []
        for station_id, aqi in self._station_aqi.items():
            totals = self._compliance_totals[station_id]
            compliance_rate = (totals[1] / totals[0] * 100) if totals[0] else 100.0
            stations.append({
                "station_id": station_id,
                "aqi": aqi,
                "aqi_category": _aqi_category(aqi),
                "active_violations": self._active_violations[station_id],
                "compliance_rate": round(compliance_rate, 1),
                "total_readings": totals[0],
            })

        total_violations = sum(self._active_violations.values())
        overall_compliance = (
            sum(v[1] for v in self._compliance_totals.values()) /
            max(sum(v[0] for v in self._compliance_totals.values()), 1)
        ) * 100

        return {
            "stations": stations,
            "total_active_violations": total_violations,
            "overall_compliance_rate": round(overall_compliance, 1),
            "recent_alerts": self._recent_alerts[-10:],
            "station_count": len(self._station_aqi),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── Core logic ────────────────────────────────────────────────────────────

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Produce and broadcast a full dashboard snapshot."""
        snapshot = self.get_dashboard_snapshot()
        await self._broadcast("dashboard.update", snapshot)
        return {"status": "ok", "snapshot": snapshot}

    async def on_event(self, event: Dict[str, Any]) -> None:
        """Handle both ``sensor.reading`` and ``violation.detected`` events."""
        event_type = event.get("_event_type", "")

        if "severity" in event:
            # This is a violation event
            station_id = event.get("station_id", "UNKNOWN")
            self._active_violations[station_id] += 1
            alert_entry = {
                "station_id": station_id,
                "pollutant": event.get("pollutant_type"),
                "severity": event.get("severity"),
                "timestamp": str(event.get("timestamp", datetime.now(timezone.utc).isoformat())),
            }
            self._add_alert(alert_entry)
            await self._broadcast("violation.new", event)
        else:
            # Sensor reading event
            station_id = event.get("station_id", "UNKNOWN")
            aqi = float(event.get("aqi", 0.0))
            self._update_aqi(station_id, aqi)
            self._record_reading(station_id, is_compliant=True)

        # Always push full snapshot after any event
        snapshot = self.get_dashboard_snapshot()
        await self._broadcast("stats.update", snapshot)


def _aqi_category(aqi: float) -> str:
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Satisfactory"
    elif aqi <= 200:
        return "Moderate"
    elif aqi <= 300:
        return "Poor"
    elif aqi <= 400:
        return "Very Poor"
    return "Severe"
