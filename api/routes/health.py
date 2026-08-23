"""System health and public risk assessment routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session, SensorReading, Violation as ViolationORM
from core.ibm_cloud import ibm_cloud_client
from core.orchestrator import orchestrator

router = APIRouter()


@router.get("", summary="System health check")
async def system_health(db: AsyncSession = Depends(get_session)) -> dict:
    """Return operational status of all EcoGuardAI subsystems."""
    # DB check
    db_ok = False
    try:
        await db.execute(select(func.now()))
        db_ok = True
    except Exception:
        pass

    # WatsonX check
    watsonx_status = await ibm_cloud_client.check_watsonx_health()

    # MQTT check
    mqtt_connected = False
    if orchestrator._mqtt:
        mqtt_connected = orchestrator._mqtt.is_connected

    # Agent check
    agent_statuses = {a.name: "running" for a in orchestrator._agents}

    return {
        "status": "healthy" if db_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "subsystems": {
            "database": "healthy" if db_ok else "unavailable",
            "mqtt": "connected" if mqtt_connected else "disconnected",
            "watsonx": watsonx_status.get("status", "unknown"),
            "orchestrator": "running" if orchestrator.is_running else "stopped",
        },
        "agents": agent_statuses,
        "version": "1.0.0",
    }


@router.get("/risk-assessment", summary="Current risk levels per station")
async def risk_assessment() -> dict:
    """Return the latest public health risk assessment for all stations."""
    from agents.dashboard_agent import DashboardAgent
    from agents.health_agent import classify_aqi

    dashboard_agent = None
    for agent in orchestrator._agents:
        if isinstance(agent, DashboardAgent):
            dashboard_agent = agent
            break

    if dashboard_agent is None:
        return {
            "assessments": [],
            "overall_risk": "UNKNOWN",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "note": "Dashboard agent not running.",
        }

    snapshot = dashboard_agent.get_dashboard_snapshot()
    assessments = []
    for station in snapshot["stations"]:
        aqi = station["aqi"]
        aqi_info = classify_aqi(aqi)
        assessments.append({
            "station_id": station["station_id"],
            "aqi": aqi,
            "risk_level": aqi_info["level"],
            "description": aqi_info["description"],
            "active_violations": station["active_violations"],
            "compliance_rate": station["compliance_rate"],
        })

    return {
        "assessments": assessments,
        "overall_risk": _overall_risk(assessments),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _overall_risk(assessments: List[dict]) -> str:
    if not assessments:
        return "UNKNOWN"
    levels = [a["risk_level"] for a in assessments]
    for level in ("HAZARDOUS", "UNHEALTHY", "MODERATE"):
        if level in levels:
            return level
    return "SAFE"
