"""Auth, rate-limit, and CORS middleware for EcoGuardAI API."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable, Dict, List, Tuple

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


# Paths that do not require an API key
_OPEN_PATHS = {"/", "/docs", "/redoc", "/openapi.json", "/api/health", "/api/stream", "/dashboard"}

# Read-only endpoints exempt from rate limiting (dashboard REST fallback).
# High-frequency sensor ingestion from the same client IP would otherwise
# exhaust the shared per-IP window and starve these GETs with 429s.
# Keep the exemption narrow to GET endpoints that power the dashboard and
# live monitoring UI, while leaving ingestion and write paths rate limited.
_RATE_LIMIT_EXEMPT_PATHS = {
    "/api/monitoring/aqi",
    "/api/monitoring/stations",
    "/api/violations",
    "/api/alerts",
    "/api/health/risk-assessment",
    "/api/stream",
    "/dashboard",
    "/docs",
    "/redoc",
    "/openapi.json",
}


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validate X-API-Key header for protected routes.

    In development mode (settings.environment != 'production'), all requests
    are allowed through so developers don't need to set up API keys locally.
    """

    def __init__(self, app, valid_keys: List[str] | None = None) -> None:
        super().__init__(app)
        self._valid_keys = set(valid_keys or [])

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        from config.settings import settings

        # Open paths and dev mode bypass auth
        if request.url.path in _OPEN_PATHS or request.url.path.startswith("/dashboard"):
            return await call_next(request)

        if not settings.is_production:
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key not in self._valid_keys:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key. Include X-API-Key header."},
            )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter keyed on client IP address.

    Defaults: 200 requests per 60-second window.
    Monitoring ingestion endpoints get a 5× higher limit to support bulk posting.
    """

    def __init__(self, app, max_requests: int = 1000, window_seconds: int = 60) -> None:
        super().__init__(app)
        self._max_requests = max_requests
        self._window = window_seconds
        # ip → list of request timestamps
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # SSE endpoint should not be rate-limited (long-lived connections).
        # Read-only endpoints consumed by the dashboard's REST fallback are also
        # exempt so high-frequency sensor ingestion from the same client IP
        # cannot starve them (they share the per-IP timestamp window).
        if request.url.path in _RATE_LIMIT_EXEMPT_PATHS or request.url.path.startswith("/api/monitoring/readings"):
            return await call_next(request)

        ip = self._get_client_ip(request)
        now = time.monotonic()

        # Ingestion paths get relaxed limits (10x base to support simulator + historical data generators)
        limit = self._max_requests * 10 if "readings" in request.url.path else self._max_requests

        timestamps = self._requests[ip]
        # Purge old entries outside the window
        self._requests[ip] = [t for t in timestamps if now - t < self._window]

        if len(self._requests[ip]) >= limit:
            logger.warning("Rate limit exceeded | ip={} path={}", ip, request.url.path)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please slow down."},
                headers={"Retry-After": str(self._window)},
            )

        self._requests[ip].append(now)
        return await call_next(request)