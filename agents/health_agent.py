"""Public Health Risk Assessment Agent."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from agents.base_agent import BaseAgent
from core.ibm_granite import GraniteClient


# ── AQI category thresholds (India NAQI) ─────────────────────────────────────
_AQI_CATEGORIES = [
    (0, 50, "SAFE", "Good air quality. No health implications."),
    (51, 100, "SAFE", "Satisfactory. Minor discomfort for sensitive individuals."),
    (101, 200, "MODERATE", "Moderate health effects for sensitive groups."),
    (201, 300, "UNHEALTHY", "Unhealthy for all. Respiratory effects common."),
    (301, 400, "HAZARDOUS", "Very unhealthy. Serious health effects."),
    (401, 500, "HAZARDOUS", "Hazardous. Emergency health conditions."),
]

# WQI parameter weights (simplified Weighted Arithmetic WQI)
_WQI_WEIGHTS = {"PH": 0.15, "BOD": 0.25, "COD": 0.20, "TSS": 0.15, "PB": 0.10, "CR": 0.10, "HG": 0.05}

# Ideal and permissible values for WQI calculation
_WQI_IDEAL = {"PH": 7.0, "BOD": 0.0, "COD": 0.0, "TSS": 0.0, "PB": 0.0, "CR": 0.0, "HG": 0.0}
_WQI_PERMISSIBLE = {"PH": 8.5, "BOD": 30.0, "COD": 250.0, "TSS": 100.0, "PB": 0.05, "CR": 0.1, "HG": 0.001}


def classify_aqi(aqi: float) -> Dict[str, str]:
    """Return AQI category and description."""
    for lo, hi, category, desc in _AQI_CATEGORIES:
        if lo <= aqi <= hi:
            return {"level": category, "description": desc}
    return {"level": "HAZARDOUS", "description": "AQI out of range."}


def calculate_wqi(water_readings: Dict[str, float]) -> float:
    """Calculate Water Quality Index using Weighted Arithmetic method.

    Args:
        water_readings: Mapping of pollutant code → concentration.

    Returns:
        WQI score (0=excellent, 100+=very poor).
    """
    if not water_readings:
        return 0.0

    total_weight = 0.0
    weighted_sum = 0.0

    for param, weight in _WQI_WEIGHTS.items():
        value = water_readings.get(param)
        if value is None:
            continue
        ideal = _WQI_IDEAL.get(param, 0.0)
        permissible = _WQI_PERMISSIBLE.get(param, 1.0)

        if permissible - ideal == 0:
            continue

        quality_rating = 100 * (value - ideal) / (permissible - ideal)
        weighted_sum += weight * quality_rating
        total_weight += weight

    if total_weight == 0:
        return 0.0
    return round(weighted_sum / total_weight, 2)


def estimate_affected_radius(concentration: float, limit: float, wind_speed_ms: float = 2.0) -> float:
    """Estimate the radius (km) of potentially affected area using a simplified Gaussian dispersion model.

    Args:
        concentration: Measured pollutant concentration.
        limit: Regulatory limit.
        wind_speed_ms: Wind speed in m/s (default: 2.0 m/s calm conditions).

    Returns:
        Estimated affected radius in kilometres.
    """
    exceedance_ratio = concentration / limit if limit else 1.0
    # Empirical estimate: base radius scaled by exceedance and inversely by wind
    base_radius_km = 0.5
    radius = base_radius_km * math.sqrt(exceedance_ratio) * (1 / max(wind_speed_ms, 0.5))
    return round(min(radius, 25.0), 2)  # cap at 25 km


class PublicHealthRiskAgent(BaseAgent):
    """Assesses public health risks from pollution data.

    Calculates AQI, WQI, estimates affected population, and uses IBM Granite
    to generate plain-language health advisories.
    """

    name = "PublicHealthRiskAgent"
    description = "Calculates AQI/WQI, estimates affected population, generates health advisories."

    def __init__(self, orchestrator: Any) -> None:
        super().__init__(orchestrator)
        self._granite = GraniteClient()

    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Perform a health risk assessment.

        Args:
            context: Should contain ``violation`` dict, optional ``air_readings``,
                     ``water_readings``, ``population_density``, ``wind_speed_ms``.

        Returns:
            Dict with ``risk_level``, ``aqi``, ``wqi``, ``affected_radius_km``,
            ``advisory``, and ``status``.
        """
        violation = context.get("violation", context)  # accept flat violation dict too
        air_readings = context.get("air_readings", {})
        water_readings = context.get("water_readings", {})
        population_density = context.get("population_density", 500)  # people/km²
        wind_speed = context.get("wind_speed_ms", 2.0)

        # Compute AQI
        aqi = context.get("aqi", 0.0)
        aqi_info = classify_aqi(aqi)

        # Compute WQI
        wqi = calculate_wqi(water_readings)

        # Estimate affected radius from the triggering violation
        measured = float(violation.get("measured_value", 0))
        limit = float(violation.get("limit_value", 1))
        radius_km = estimate_affected_radius(measured, limit, wind_speed)

        # Estimate affected population
        affected_area_km2 = math.pi * radius_km ** 2
        affected_population = int(affected_area_km2 * population_density)

        # Determine overall risk level
        if aqi > 300 or wqi > 150:
            risk_level = "HAZARDOUS"
        elif aqi > 200 or wqi > 100:
            risk_level = "UNHEALTHY"
        elif aqi > 100 or wqi > 50:
            risk_level = "MODERATE"
        else:
            risk_level = "SAFE"

        # Generate health advisory via IBM Granite
        pollutants_context = {
            f"{violation.get('pollutant_type')} ({violation.get('unit', '')})": f"{measured} (limit: {limit})",
            **{k: f"{v}" for k, v in air_readings.items()},
        }
        population_context = {
            "radius_km": radius_km,
            "density": population_density,
            "vulnerable_groups": ["children under 12", "elderly over 65", "respiratory patients"],
        }

        advisory = await self._granite.assess_health_risk(pollutants_context, population_context)

        result = {
            "status": "ok",
            "risk_level": risk_level,
            "aqi": round(aqi, 1),
            "aqi_category": aqi_info["level"],
            "aqi_description": aqi_info["description"],
            "wqi": wqi,
            "affected_radius_km": radius_km,
            "affected_population_estimate": affected_population,
            "advisory": advisory,
            "station_id": violation.get("station_id"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Emit dashboard update
        await self.orchestrator.emit("health.assessment", result)
        self.log(
            f"Health assessment | risk={risk_level} AQI={aqi:.1f} radius={radius_km}km pop~{affected_population}"
        )
        return result

    async def on_event(self, event: Dict[str, Any]) -> None:
        """Handle a ``violation.detected`` event."""
        await self.run({"violation": event, "aqi": event.get("aqi", 0.0)})