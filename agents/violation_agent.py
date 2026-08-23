"""Violation Detection & Compliance Agent — ML anomaly detection + Granite reports."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import numpy as np
    from sklearn.ensemble import IsolationForest

    _ML_AVAILABLE = True
except ImportError:
    logger.warning(
        "numpy/scikit-learn not available (no pre-built wheel for this Python "
        "version); ViolationDetectionAgent will use threshold-only detection."
    )
    _ML_AVAILABLE = False

from agents.base_agent import BaseAgent
from core.ibm_granite import GraniteClient


class ViolationDetectionAgent(BaseAgent):
    """Detects anomalous sensor readings using IsolationForest and classifies severity.

    On each ``emission.violation`` event it:
    1. Updates the per-station anomaly model with recent readings.
    2. Scores the new reading — confirms or dismisses the violation (ML when available).
    3. Uses IBM Granite to draft a formal violation report.
    4. Emits a ``violation.detected`` event for downstream agents.
    """

    name = "ViolationDetectionAgent"
    description = "ML-based violation confirmation, severity classification, and Granite report generation."

    # Number of recent readings kept in the rolling window per station
    WINDOW_SIZE = 100

    def __init__(self, orchestrator: Any) -> None:
        super().__init__(orchestrator)
        self._granite = GraniteClient()
        # Per-station sliding window: station_id → list of (value, limit)
        self._windows: Dict[str, List[tuple]] = {}
        # Per-station IsolationForest models (lazily created)
        self._models: Dict[str, Any] = {}

    # ── Helpers ────────────────────────────────────────────────────────────

    def _update_window(self, station_id: str, value: float, limit: float) -> None:
        """Append the reading to the station's rolling window."""
        window = self._windows.setdefault(station_id, [])
        window.append((value, limit))
        if len(window) > self.WINDOW_SIZE:
            window.pop(0)

    def _is_anomaly(self, station_id: str, value: float, limit: float) -> bool:
        """Return True if the reading is flagged as anomalous.

        Uses IsolationForest when scikit-learn is available, otherwise falls back
        to a simple exceedance check (always True when above the limit).
        """
        window = self._windows.get(station_id, [])
        if not _ML_AVAILABLE or len(window) < 10:
            return True

        X = np.array(window)
        model = self._models.get(station_id)
        if model is None or len(window) % 20 == 0:
            model = IsolationForest(
                contamination=0.1, random_state=42, n_estimators=50
            )
            model.fit(X)
            self._models[station_id] = model

        ratio = value / limit if limit else 0.0
        score = model.decision_function([[value, ratio]])[0]
        return bool(score < -0.05)

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
        """Process a list of pre-flagged emission violations.

        Args:
            context: Must contain ``violations`` list of violation dicts.

        Returns:
            Dict with confirmed violation records.
        """
        violations = context.get("violations", [])
        confirmed: List[Dict[str, Any]] = []

        for v in violations:
            station_id = v.get("station_id", "UNKNOWN")
            measured = float(v.get("measured_value", 0))
            limit = float(v.get("limit_value", 1))

            self._update_window(station_id, measured, limit)
            anomalous = self._is_anomaly(station_id, measured, limit)

            if not anomalous:
                self.log(
                    f"Reading for {station_id} not anomalous; skipping.",
                    level="debug",
                )
                continue

            severity = self._classify_severity(measured, limit)
            v["severity"] = severity

            # Generate Granite report (async LLM call; stub when unconfigured)
            report = await self._granite.generate_violation_report(v)
            v["report_text"] = report

            confirmed.append(v)
            await self.orchestrator.emit("violation.detected", v)
            self.log(f"Confirmed violation | station={station_id} severity={severity}")

        return {"status": "ok", "confirmed_violations": len(confirmed)}

    async def on_event(self, event: Dict[str, Any]) -> None:
        """Handle an ``emission.violation`` event."""
        await self.run({"violations": [event]})
