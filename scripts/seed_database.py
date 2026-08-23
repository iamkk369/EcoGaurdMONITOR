"""Database seeder — populates industrial units, 30 days of readings, violations, alerts."""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure project root is on path when run as a script
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.database import (
    AsyncSessionLocal,
    Base,
    IndustrialUnit,
    init_db,
    _engine,
)

STATIONS_FILE = Path(__file__).parent.parent / "data" / "sample_sensors.json"
LIMITS_FILE = Path(__file__).parent.parent / "data" / "pollutant_limits.json"
POPULATION_FILE = Path(__file__).parent.parent / "data" / "population_zones.json"


def _get_limit(pollutant: str, industry_type: str, limits_data: Dict) -> float:
    ptype = pollutant.upper()
    
    # 1. Check for CETP-specific limits first if applicable
    if industry_type == "cetp":
        cetp_limits = limits_data.get("cetp", {})
        if ptype in cetp_limits:
            entry = cetp_limits[ptype]
            return float(entry.get("limit") or entry.get("max", 0))

    # 2. Check for other industry-specific limits
    industry_limits = limits_data.get("industry_specific", {}).get(industry_type, {})
    if ptype in industry_limits:
        entry = industry_limits[ptype]
        return float(entry.get("limit") or entry.get("max", 0))

    # 3. Fallback to general category limits
    for cat in ("air", "water", "heavy_metals"):
        cat_data = limits_data.get(cat, {})
        if ptype in cat_data:
            entry = cat_data[ptype]
            if isinstance(entry, dict):
                return float(entry.get("limit") or entry.get("max", 0))
            return float(entry)
    return 0.0


async def seed(session: AsyncSession) -> None:
    """Seed the database with industrial units and their metadata."""
    with STATIONS_FILE.open() as f:
        stations_data: List[Dict[str, Any]] = json.load(f)
    with LIMITS_FILE.open() as f:
        limits_data: Dict[str, Any] = json.load(f)
    with POPULATION_FILE.open() as f:
        population_data: List[Dict[str, Any]] = json.load(f)

    population_map = {item["station_id"]: item for item in population_data}

    print(f"Seeding {len(stations_data)} industrial units…")

    # ── Industrial units ──────────────────────────────────────────────────────
    for s in stations_data:
        station_id = s["station_id"]
        existing = await session.execute(
            select(IndustrialUnit).where(IndustrialUnit.name == station_id)
        )
        if existing.scalar_one_or_none():
            continue

        unit = IndustrialUnit(
            name=station_id,
            location=s["location"],
            industry_type=s["industry_type"],
            latitude=s["latitude"],
            longitude=s["longitude"],
            contact_email=s.get("contact_email", ""),
            permitted_limits={
                sensor["pollutant_type"].upper(): _get_limit(
                    sensor["pollutant_type"], s["industry_type"], limits_data
                )
                for sensor in s["sensors"]
            },
            population_data=population_map.get(station_id, {}),
        )
        session.add(unit)

    await session.commit()
    print("Industrial units seeded successfully. OK")


async def main() -> None:
    print("This script will DELETE and re-create the database schema.")
    print("Continue? [y/N]")
    if input().lower() != "y":
        print("Aborted.")
        return

    print("Dropping and re-creating database schema…")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("Schema reset successfully.")

    async with AsyncSessionLocal() as session:
        await seed(session)
    
    print("\nHistorical data generation is now a separate step.")
    print("To generate historical data, run:")
    print("  python scripts/generate_historical_data.py")
    print("\nSeeding process complete.")


if __name__ == "__main__":
    asyncio.run(main())