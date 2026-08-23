"""IBM Granite LLM integration wrapper using ibm-watsonx-ai SDK."""
from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any, Dict

from loguru import logger

try:
    from ibm_watsonx_ai import APIClient, Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
    _WATSONX_AVAILABLE = True
except ImportError:
    _WATSONX_AVAILABLE = False
    logger.warning("ibm-watsonx-ai not installed; GraniteClient will run in stub mode.")

from config.settings import settings


class GraniteClient:
    """Async wrapper around IBM WatsonX Granite LLM.

    All public methods are async and safe for use inside FastAPI request handlers
    and asyncio tasks.  Long-running synchronous SDK calls are offloaded to the
    default thread-pool executor to avoid blocking the event loop.
    """

    _PARAMS = {
        "decoding_method": "greedy",
        "max_new_tokens": 800,
        "min_new_tokens": 50,
        "temperature": 0.3,
        "repetition_penalty": 1.05,
    } if _WATSONX_AVAILABLE else {}

    def __init__(self) -> None:
        self._model: Any = None
        self._initialised = False

    def _init_model(self) -> None:
        """Lazy-initialise the WatsonX model client (called in thread pool).

        On any authentication or connection failure the client falls back to
        stub mode rather than propagating an exception into the async event loop.
        """
        if self._initialised:
            return
        if not _WATSONX_AVAILABLE:
            logger.warning("GraniteClient running in STUB MODE — ibm-watsonx-ai not importable.")
            self._initialised = True
            return

        # Detect placeholder credentials before attempting a network call
        placeholder = settings.ibm_cloud_api_key in ("dev-placeholder", "your_ibm_cloud_api_key", "")
        if placeholder:
            logger.warning(
                "GraniteClient running in STUB MODE — IBM_CLOUD_API_KEY is not configured. "
                "Set a real key in .env to enable live LLM calls."
            )
            self._initialised = True
            return

        try:
            credentials = Credentials(
                url=settings.ibm_watson_url,
                api_key=settings.ibm_cloud_api_key,
            )
            client = APIClient(credentials)
            self._model = ModelInference(
                model_id=settings.ibm_granite_model_id,
                api_client=client,
                project_id=settings.watsonx_project_id,
                params=self._PARAMS,
            )
            self._initialised = True
            logger.info("GraniteClient initialised | model={}", settings.ibm_granite_model_id)
        except Exception as exc:
            logger.warning(
                "GraniteClient failed to initialise ({}); falling back to STUB MODE. "
                "Check IBM_CLOUD_API_KEY and WATSONX_PROJECT_ID.",
                exc,
            )
            self._model = None
            self._initialised = True

    async def _generate(self, prompt: str) -> str:
        """Run a synchronous generate() call in the thread pool."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init_model)

        if not _WATSONX_AVAILABLE or self._model is None:
            return self._stub_response(prompt)

        generate_fn = partial(self._model.generate_text, prompt=prompt)
        result: str = await loop.run_in_executor(None, generate_fn)
        return result.strip()

    # ── Public API ────────────────────────────────────────────────────────────

    async def analyze_pollution_data(self, sensor_data: Dict[str, Any]) -> str:
        """Send structured sensor readings to Granite for NL analysis.

        Args:
            sensor_data: Dict with keys station_id, readings (list of pollutant/value pairs),
                         timestamp, and optional location info.

        Returns:
            Natural-language analysis paragraph.
        """
        readings_text = "\n".join(
            f"  - {r.get('pollutant_type', 'UNKNOWN')}: {r.get('value', 'N/A')} {r.get('unit', '')}"
            for r in sensor_data.get("readings", [])
        )
        prompt = (
            "You are an environmental compliance expert analysing industrial sensor data "
            "from the Vapi-Ankleshwar-Vatva chemical corridor in Gujarat, India.\n\n"
            f"Station: {sensor_data.get('station_id', 'Unknown')}\n"
            f"Timestamp: {sensor_data.get('timestamp', 'Unknown')}\n"
            f"Sensor readings:\n{readings_text}\n\n"
            "Provide a concise technical analysis (3-5 sentences) of the pollution levels, "
            "identify any concerning trends, and state whether immediate action is required."
        )
        return await self._generate(prompt)

    async def assess_health_risk(
        self, pollutants: Dict[str, float], population: Dict[str, Any]
    ) -> str:
        """Generate a public health risk assessment.

        Args:
            pollutants: Mapping of pollutant code → concentration value with units.
            population: Dict with keys density (people/km²), vulnerable_groups (list), radius_km.

        Returns:
            Health advisory string.
        """
        pollutant_lines = "\n".join(
            f"  - {k}: {v}" for k, v in pollutants.items()
        )
        prompt = (
            "You are a public health expert assessing industrial pollution risks.\n\n"
            f"Pollutant concentrations:\n{pollutant_lines}\n\n"
            f"Affected area: {population.get('radius_km', 'unknown')} km radius\n"
            f"Population density: {population.get('density', 'unknown')} people/km²\n"
            f"Vulnerable groups present: {', '.join(population.get('vulnerable_groups', []))}\n\n"
            "Provide a public health risk assessment covering:\n"
            "1. Overall risk level (SAFE / MODERATE / UNHEALTHY / HAZARDOUS)\n"
            "2. Specific health impacts expected\n"
            "3. Recommended protective actions for residents\n"
            "Keep the response under 200 words."
        )
        return await self._generate(prompt)

    async def generate_violation_report(self, violation: Dict[str, Any]) -> str:
        """Generate a formal regulatory violation report.

        Args:
            violation: Dict with station_id, pollutant_type, measured_value,
                       limit_value, unit, severity, timestamp.

        Returns:
            Formal violation report text suitable for submission to CPCB/GPCB.
        """
        exceedance_pct = 0.0
        if violation.get("limit_value"):
            exceedance_pct = (
                (violation["measured_value"] - violation["limit_value"])
                / violation["limit_value"]
            ) * 100

        prompt = (
            "Generate a formal environmental violation report for submission to GPCB "
            "(Gujarat Pollution Control Board).\n\n"
            f"Violation Details:\n"
            f"  Station ID: {violation.get('station_id')}\n"
            f"  Pollutant: {violation.get('pollutant_type')}\n"
            f"  Measured Value: {violation.get('measured_value')} {violation.get('unit')}\n"
            f"  Regulatory Limit: {violation.get('limit_value')} {violation.get('unit')}\n"
            f"  Exceedance: {exceedance_pct:.1f}%\n"
            f"  Severity: {violation.get('severity')}\n"
            f"  Date/Time: {violation.get('timestamp')}\n\n"
            "The report must include: (1) Summary of violation, (2) Applicable regulation "
            "reference, (3) Environmental impact assessment, (4) Recommended corrective actions, "
            "(5) Compliance timeline. Use formal regulatory language."
        )
        return await self._generate(prompt)

    async def recommend_action(self, context: Dict[str, Any]) -> str:
        """Recommend regulatory or operational action based on context.

        Args:
            context: Dict with situation description, available actions, constraints.

        Returns:
            Recommended action plan string.
        """
        prompt = (
            "You are a senior environmental regulatory advisor for the GPCB.\n\n"
            f"Current situation:\n{json.dumps(context, indent=2, default=str)}\n\n"
            "Based on the above context, recommend the most appropriate regulatory and "
            "operational actions. Prioritise human health and environmental protection. "
            "Be specific, actionable, and cite relevant Indian environmental regulations "
            "(EPA 1986, Water Act 1974, Air Act 1981) where applicable. "
            "Limit response to 250 words."
        )
        return await self._generate(prompt)

    # ── Stub mode ─────────────────────────────────────────────────────────────

    @staticmethod
    def _stub_response(prompt: str) -> str:
        """Return a deterministic stub when the SDK is unavailable."""
        snippet = prompt[:80].replace("\n", " ")
        return (
            f"[STUB MODE] IBM Granite LLM is not configured. "
            f"Prompt preview: '{snippet}...' — "
            "Install ibm-watsonx-ai and set IBM_CLOUD_API_KEY + WATSONX_PROJECT_ID to enable real LLM calls."
        )
