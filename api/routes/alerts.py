"""Alerts routes."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session, Alert as AlertORM
from models.alert import AlertType

router = APIRouter()


@router.get("", summary="List alerts")
async def list_alerts(
    alert_type: Optional[AlertType] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
) -> List[dict]:
    """Return the most recent alerts with optional filters."""
    stmt = select(AlertORM).order_by(desc(AlertORM.created_at)).offset(offset).limit(limit)

    if alert_type:
        stmt = stmt.where(AlertORM.alert_type == alert_type.value)
    if status:
        stmt = stmt.where(AlertORM.status == status.upper())

    result = await db.execute(stmt)
    alerts = result.scalars().all()

    return [
        {
            "id": a.id,
            "violation_id": a.violation_id,
            "alert_type": a.alert_type,
            "recipient": a.recipient,
            "message": a.message,
            "status": a.status,
            "sent_at": a.sent_at.isoformat() if a.sent_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in alerts
    ]


@router.post("/test", summary="Send a test alert")
async def send_test_alert(
    alert_type: AlertType = Query(default=AlertType.EMAIL),
    recipient: str = Query(default="admin@ecoguardai.com"),
) -> dict:
    """Dispatch a test alert through the RegulatoryAlertAgent."""
    from agents.alert_agent import RegulatoryAlertAgent
    from core.orchestrator import orchestrator

    agent: Optional[RegulatoryAlertAgent] = None
    for a in orchestrator._agents:
        if isinstance(a, RegulatoryAlertAgent):
            agent = a
            break

    if agent is None:
        raise HTTPException(status_code=503, detail="RegulatoryAlertAgent not running.")

    test_violation = {
        "station_id": "TEST-STATION",
        "pollutant_type": "SO2",
        "measured_value": 250.0,
        "limit_value": 80.0,
        "unit": "µg/m³",
        "severity": "HIGH",
        "timestamp": "2024-01-01T00:00:00Z",
        "report_text": "This is a test alert from EcoGuardAI.",
    }

    if alert_type == AlertType.EMAIL:
        success = await agent.send_email_alert(test_violation, [recipient])
    elif alert_type == AlertType.WEBHOOK:
        success = await agent.send_webhook_alert(test_violation, recipient)
    else:
        success = True  # Dashboard/SMS — emit to orchestrator
        await orchestrator.emit("dashboard.alert", test_violation)

    return {"status": "sent" if success else "failed", "alert_type": alert_type.value, "recipient": recipient}
