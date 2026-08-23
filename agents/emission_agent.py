"""Emission Monitoring Agent — sensor ingestion, AQI computation, threshold checks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from agents.base_agent import BaseAgent

# Pollutants that participate in the (linear sub-index) AQI computation.
AQI_POLLUTANTS = {"SO2", "NOX", "PM25", "PM10", "CO", "O3", "NH3"}

_LIMITS_FILE = Path(__file__).resolve().parents[1] / "data" / "pollutant_limits.json"


def calculate_aqi(readings: List[Dict[str, Any]], limits: Dict[str, Any]) -> float:
    """Compute a simple linear sub-index AQI from the current readings.

    For every AQI-relevant pollutant a sub-index of ``value / limit * 100`` is
    calculated and the maximum is returned. Returns 0.0 when no AQI pollutant
    has a known limit.
    """
    flat: Dict[str, float] = {}
    for _category, pollutants in (limits or {}).items():
        if isinstance(pollutants, dict):
            for ptype, info in pollutants.items():
                if isinstance(info, dict) and info.get("limit"):
                    flat[ptype] = float(info["limit"])
                elif isinstance(info, (int, float)):
                    flat[ptype] = float(info)

    aqi = 0.0
    for r in readings or []:
        ptype = str(r.get("pollutant_type", "")).upper()
        if ptype not in AQI_POLLUTANTS:
            continue
        limit = flat.get(ptype)
        value = r.get("value")
        if limit and value is not None:
            try:
                aqi = max(aqi, float(value) / limit * 100.0)
            except (TypeError, ValueError):
                continue
    return round(aqi, 1)


class EmissionMonitoringAgent(BaseAgent):
    """Ingests sensor readings, computes AQI, and flags threshold exceedances."""

    name = "EmissionMonitoringAgent"
    description = (
        "Ingests air/water/heavy-metal sensor readings, computes AQI, and emits "
        "threshold-based emission.violation events."
    )

    def __init__(self, orchestrator: Any) -> None:
        super().__init__(orchestrator)
        self._limits: Dict[str, Any] = self._load_limits()

    # ── Setup ──────────────────────────────────────────────────────────────

    def _load_limits(self) -> Dict[str, Any]:
        """Load permitted-limit configuration bundled with the project."""
        try:
            with open(_LIMITS_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            logger.info("Loaded permitted limits from {}", _LIMITS_FILE.name)
            return data
        except Exception as exc:
            logger.warning("Could not load pollutant limits ({}); using empty set.", exc)
            return {
                "air": {}, "water": {},
                "heavy_metals": {}, "industry_specific": {},
            }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _limit_for(self, pollutant_type: str) -> float:
        """Return the permitted limit for a pollutant (0.0 when unknown)."""
        ptype = str(pollutant_type or "").upper()
        for _category, pollutants in self._limits.items():
            if not isinstance(pollutants, dict):
                continue
            info = pollutants.get(ptype)
            if isinstance(info, dict):
                limit = info.get("limit") or info.get("max")
                if limit:
                    return float(limit)
            elif isinstance(info, (int, float)):
                return float(info)
        return 0.0

    def _classify_severity(self, measured: float, limit: float) -> str:
        """Classify exceedance severity from the measured/limit ratio."""
        ratio = measured / limit if limit else 0.0
        if ratio < 1.2:
            return "LOW"
        if ratio < 1.5:
            return "MEDIUM"
        if ratio < 2.0:
            return "HIGH"
        return "CRITICAL"

    # ── Processing ─────────────────────────────────────────────────────────

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a sensor reading batch against permitted limits.

        Args:
            context: Reading payload with ``station_id`` and ``readings`` list.

        Returns:
            Dict with ``status``, ``station_id``, ``aqi`` and ``violations``.
        """
        station_id = context.get("station_id", "UNKNOWN")
        readings = context.get("readings", []) or []
        violations: List[Dict[str, Any]] = []

        for r in readings:
            ptype = str(r.get("pollutant_type", "")).upper()
            if not ptype:
                continue
            limit = self._limit_for(ptype)
            if not limit:
                continue
            try:
                value = float(r.get("value", 0))
            except (TypeError, ValueError):
                continue
            if value <= limit:
                continue

            severity = self._classify_severity(value, limit)
            violations.append({
                "station_id": context.get("station_id", "UNKNOWN"),
                "industry_type": context.get("industry_type", "general"),
                "pollutant_type": ptype,
                "measured_value": value,
                "limit_value": limit,
                "unit": r.get("unit", ""),
                "severity": severity,
                "timestamp": r.get("timestamp")
                or datetime.now(timezone.utc).isoformat(),
                "status": "OPEN",
            })

        aqi = calculate_aqi(readings, self._limits)

        for violation in violations:
            await self.orchestrator.emit("emission.violation", violation)

        if violations:
            self.log(
                f"{len(violations)} exceedance(s) at {station_id} | AQI={aqi}"
            )

        return {
            "status": "ok",
            "station_id": station_id,
            "aqi": aqi,
            "violations": violations,
        }

    async def on_event(self, event: Dict[str, Any]) -> None:
        """Handle a ``sensor.reading`` event (batch or flat single reading)."""
        if event.get("readings"):
            await self.run(event)
        elif event.get("pollutant_type"):
            await self.run({**event, "readings": [event]})
