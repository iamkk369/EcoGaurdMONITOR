"""IBM Cloud services client helpers."""
from __future__ import annotations

import asyncio
from functools import partial
from typing import Any, Dict, Optional

from loguru import logger

try:
    from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False
    logger.warning("ibm-cloud-sdk-core not installed; IBMCloudClient in stub mode.")

from config.settings import settings


class IBMCloudClient:
    """Thin async wrapper around IBM Cloud SDK core utilities.

    Provides helpers for token retrieval, resource listing, and health
    checks against IBM Cloud services used by EcoGuardAI.
    """

    def __init__(self) -> None:
        self._authenticator: Optional[Any] = None
        self._token: Optional[str] = None

    def _get_authenticator(self) -> Any:
        if self._authenticator is None:
            if not _SDK_AVAILABLE:
                return None
            self._authenticator = IAMAuthenticator(settings.ibm_cloud_api_key)
        return self._authenticator

    async def get_iam_token(self) -> Optional[str]:
        """Retrieve a fresh IAM bearer token (async, thread-pool offloaded)."""
        loop = asyncio.get_event_loop()

        def _fetch() -> Optional[str]:
            auth = self._get_authenticator()
            if auth is None:
                return None
            token_manager = auth.token_manager  # type: ignore[attr-defined]
            token_manager.paced_request_token()
            return token_manager.get_token()

        try:
            self._token = await loop.run_in_executor(None, _fetch)
            return self._token
        except Exception as exc:
            logger.error("Failed to retrieve IAM token: {}", exc)
            return None

    async def check_watsonx_health(self) -> Dict[str, Any]:
        """Ping the WatsonX endpoint and return a status dict."""
        import aiohttp

        url = f"{settings.ibm_watson_url}/ml/v4/models"
        token = await self.get_iam_token()
        if not token:
            return {"status": "unavailable", "reason": "No IAM token"}

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        params = {"version": "2023-05-29"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        return {"status": "healthy", "http_code": resp.status}
                    text = await resp.text()
                    return {"status": "degraded", "http_code": resp.status, "detail": text[:200]}
        except Exception as exc:
            logger.error("WatsonX health check failed: {}", exc)
            return {"status": "unreachable", "reason": str(exc)}

    async def list_projects(self) -> Dict[str, Any]:
        """Return metadata about accessible WatsonX projects."""
        import aiohttp

        url = f"{settings.ibm_watson_url}/v2/projects"
        token = await self.get_iam_token()
        if not token:
            return {"projects": [], "error": "No IAM token"}

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        params = {"version": "2023-05-29"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {"projects": data.get("resources", []), "total": data.get("total_results", 0)}
                    return {"projects": [], "error": f"HTTP {resp.status}"}
        except Exception as exc:
            logger.error("Failed to list WatsonX projects: {}", exc)
            return {"projects": [], "error": str(exc)}


# Singleton
ibm_cloud_client = IBMCloudClient()
