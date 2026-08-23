"""Monitoring routes: stations, readings, AQI."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_session, SensorReading, IndustrialUnit
from core.orchestrator import orchestrator
from models.sensor_reading import SensorReadingCreate, SensorReadingResponse, BulkSensorReading

router = APIRouter()


@router.get("/stations", summary="List all monitoring stations")
async def list_stations(
    db: AsyncSession = Depends(get_session),
) -> List[dict]:
    """Return all registered industrial monitoring stations."""
    result = await db.execute(select(IndustrialUnit).order_by(IndustrialUnit.name))
    units = result.scalars().all()
    return [
        {
            "id": u.id,
            "name": u.name,
            "location": u.location,
            "industry_type": u.industry_type,
            "latitude": u.latitude,
            "longitude": u.longitude,
            "permitted_limits": u.permitted_limits,
        }
        for u in units
    ]


@router.get("/stations/{station_id}/readings", summary="Recent readings for a station")
async def get_station_readings(
    station_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    pollutant: Optional[str] = Query(default=None, description="Filter by pollutant type"),
    db: AsyncSession = Depends(get_session),
) -> List[dict]:
    """Return the most recent sensor readings for a given station."""
    stmt = (
        select(SensorReading)
        .where(SensorReading.station_id == station_id)
        .order_by(desc(SensorReading.timestamp))
        .offset(offset)
        .limit(limit)
    )
    if pollutant:
        stmt = stmt.where(SensorReading.pollutant_type == pollutant.upper())

    result = await db.execute(stmt)
    readings = result.scalars().all()

    if not readings and offset == 0:
        # Check if station exists
        exists = await db.execute(
            select(IndustrialUnit).where(IndustrialUnit.name == station_id)
        )
        if not exists.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"Station '{station_id}' not found.")

    return [
        {
            "id": r.id,
            "station_id": r.station_id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "pollutant_type": r.pollutant_type,
            "value": r.value,
            "unit": r.unit,
            "location_lat": r.location_lat,
            "location_lon": r.location_lon,
        }
        for r in readings
    ]


@router.post("/readings", status_code=201, summary="Ingest a new sensor reading")
async def ingest_reading(
    payload: SensorReadingCreate,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Persist a sensor reading and trigger the agent pipeline."""
    reading = SensorReading(
        station_id=payload.station_id,
        pollutant_type=payload.pollutant_type,
        value=payload.value,
        unit=payload.unit,
        timestamp=payload.timestamp or datetime.now(timezone.utc),
        location_lat=payload.location_lat,
        location_lon=payload.location_lon,
        sensor_metadata=payload.sensor_metadata or {},
    )
    db.add(reading)
    await db.flush()

    # Emit into agent pipeline
    await orchestrator.emit("sensor.reading", payload.model_dump(mode="json"))

    return {"id": reading.id, "status": "accepted", "station_id": payload.station_id}


import asyncio
from loguru import logger
@router.post("/readings/bulk", status_code=201, summary="Bulk ingest sensor readings")
async def ingest_bulk_readings(
    payload: BulkSensorReading,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Persist a batch of sensor readings efficiently and trigger agent pipeline."""
    readings_to_create = [
        SensorReading(
            station_id=item.station_id,
            pollutant_type=item.pollutant_type,
            value=item.value,
            unit=item.unit,
            timestamp=item.timestamp or datetime.now(timezone.utc),
            location_lat=item.location_lat,
            location_lon=item.location_lon,
            sensor_metadata=item.sensor_metadata or {},
        )
        for item in payload.readings
    ]

    if not readings_to_create:
        return {"accepted": 0, "source": payload.source, "status": "empty"}

    try:
        db.add_all(readings_to_create)
        await db.flush()

        # Convert readings to dicts *after* flushing, so they have IDs
        readings_as_dicts = [r.to_dict() for r in readings_to_create]

        if readings_as_dicts:
            await asyncio.gather(
                *(orchestrator.emit("sensor.reading", reading_dict) for reading_dict in readings_as_dicts)
            )
        
        return {
            "accepted": len(readings_to_create),
            "source": payload.source,
            "status": "processing",
        }

    except Exception as e:
        logger.error(f"Bulk ingest failed: {e}", exc_info=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error during bulk ingest: {e}")


@router.get("/aqi", summary="Current AQI for all stations")
async def get_all_aqi() -> dict:
    """Return the latest AQI values for all known stations from the DashboardAgent."""
    from agents.dashboard_agent import DashboardAgent

    for agent in orchestrator._agents:
        if isinstance(agent, DashboardAgent):
            snapshot = agent.get_dashboard_snapshot()
            return {
                "stations": [
                    {"station_id": s["station_id"], "aqi": s["aqi"], "category": s["aqi_category"]}
                    for s in snapshot["stations"]
                ],
                "updated_at": snapshot["updated_at"],
            }

    return {"stations": [], "updated_at": datetime.now(timezone.utc).isoformat(), "note": "Dashboard agent not running."}