"""Violations routes."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session, Violation as ViolationORM
from models.violation import ViolationResponse, ViolationSeverity, ViolationStatus

router = APIRouter()


@router.get("", summary="List violations with optional filters")
async def list_violations(
    severity: Optional[ViolationSeverity] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    from_date: Optional[datetime] = Query(default=None),
    to_date: Optional[datetime] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_session),
) -> List[dict]:
    """Return violations with optional filters."""
    stmt = select(ViolationORM).order_by(desc(ViolationORM.timestamp))

    if severity:
        stmt = stmt.where(ViolationORM.severity == severity.value)
    if station_id:
        stmt = stmt.where(ViolationORM.station_id == station_id)
    if status:
        stmt = stmt.where(ViolationORM.status == status.upper())
    if from_date:
        stmt = stmt.where(ViolationORM.timestamp >= from_date)
    if to_date:
        stmt = stmt.where(ViolationORM.timestamp <= to_date)

    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    violations = result.scalars().all()

    return [
        {
            "id": v.id,
            "station_id": v.station_id,
            "pollutant_type": v.pollutant_type,
            "measured_value": v.measured_value,
            "limit_value": v.limit_value,
            "unit": v.unit,
            "severity": v.severity,
            "status": v.status,
            "timestamp": v.timestamp.isoformat() if v.timestamp else None,
            "report_text": v.report_text,
        }
        for v in violations
    ]


@router.get("/{violation_id}", summary="Violation detail")
async def get_violation(
    violation_id: int,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Return a single violation record by ID."""
    result = await db.execute(
        select(ViolationORM).where(ViolationORM.id == violation_id)
    )
    v = result.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail=f"Violation {violation_id} not found.")

    return {
        "id": v.id,
        "station_id": v.station_id,
        "pollutant_type": v.pollutant_type,
        "measured_value": v.measured_value,
        "limit_value": v.limit_value,
        "unit": v.unit,
        "severity": v.severity,
        "status": v.status,
        "timestamp": v.timestamp.isoformat() if v.timestamp else None,
        "report_text": v.report_text,
        "notes": v.notes,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }


@router.patch("/{violation_id}/status", summary="Update violation status")
async def update_violation_status(
    violation_id: int,
    status: ViolationStatus,
    notes: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Update the status of a violation (OPEN → ACKNOWLEDGED → RESOLVED)."""
    result = await db.execute(
        select(ViolationORM).where(ViolationORM.id == violation_id)
    )
    v = result.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail=f"Violation {violation_id} not found.")

    from datetime import timezone
    v.status = status.value
    if notes:
        v.notes = notes
    v.updated_at = datetime.now(timezone.utc)

    return {"id": violation_id, "status": v.status, "updated_at": v.updated_at.isoformat()}
