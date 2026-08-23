"""FastAPI application entry point for EcoGuardAI."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from config.settings import settings
from config.logging_config import setup_logging
from core.database import init_db, shutdown_db
from core.orchestrator import orchestrator
from api.middleware import APIKeyMiddleware, RateLimitMiddleware
from api.routes import monitoring, violations, alerts, health as health_routes


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: DB init, orchestrator start/stop."""
    setup_logging(
        log_level=settings.log_level,
        json_format=settings.is_production,
    )
    logger.info("EcoGuardAI starting up…")

    try:
        await init_db()
        logger.info("Database schema ready.")
    except Exception as exc:
        logger.error("Database init failed (continuing without DB): {}", exc)

    try:
        await orchestrator.start()
    except Exception as exc:
        logger.error("Orchestrator start failed: {}", exc)

    yield  # Application is live

    logger.info("EcoGuardAI shutting down…")
    await orchestrator.stop()
    await shutdown_db()


# ── App factory ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="EcoGuardAI",
    description=(
        "Agentic AI Platform for Smart Industrial Pollution Monitoring — "
        "Golden Corridor (Vapi–Ankleshwar–Vatva), powered by IBM Granite LLM."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# APIKeyMiddleware: enforces X-API-Key header in production mode.
# In development (default) all requests pass through — no key needed locally.
# Set VALID_API_KEYS=key1,key2 and ENVIRONMENT=production to enable enforcement.
app.add_middleware(APIKeyMiddleware, valid_keys=settings.valid_api_keys)
app.add_middleware(RateLimitMiddleware, max_requests=200, window_seconds=60)

# ── Static files & dashboard ──────────────────────────────────────────────────

import os
if os.path.isdir("dashboard"):
    app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(monitoring.router, prefix="/api/monitoring", tags=["Monitoring"])
app.include_router(violations.router, prefix="/api/violations", tags=["Violations"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["Alerts"])
app.include_router(health_routes.router, prefix="/api/health", tags=["Health & Risk"])

# ── SSE stream ────────────────────────────────────────────────────────────────

@app.get("/api/stream", summary="Live dashboard SSE feed")
async def sse_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events endpoint consumed by the dashboard frontend.

    Pushes real-time violation, AQI, and stats updates to connected browsers.
    """
    from agents.dashboard_agent import DashboardAgent

    # Find the running DashboardAgent in the orchestrator
    dashboard: DashboardAgent | None = None
    for agent in orchestrator._agents:
        if isinstance(agent, DashboardAgent):
            dashboard = agent
            break

    if dashboard is None:
        async def _empty():
            yield "data: {\"error\": \"Dashboard agent not running\"}\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    queue = dashboard.subscribe()

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Send initial snapshot immediately
            snapshot = dashboard.get_dashboard_snapshot()
            yield f"data: {json.dumps({'type': 'snapshot', 'data': snapshot})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {message}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping
                    yield ": keepalive\n\n"
        finally:
            dashboard.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root() -> HTMLResponse:
    return HTMLResponse(
        '<meta http-equiv="refresh" content="0;url=/docs">', status_code=200
    )