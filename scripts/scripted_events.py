"""
Trigger scripted pollution events for demonstration and testing.

This script allows triggering three types of events:
1.  'normal': Resets a station's readings to baseline.
2.  'warning': Causes a station to emit readings that are moderately high,
               triggering a 'WARNING' level violation.
3.  'critical': Causes a station to emit readings far above the legal limit,
                triggering a 'CRITICAL' level violation.
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

import httpx
import typer

# Add parent path to import simulator logic
sys.path.append(str(Path(__file__).parent))
from simulate_sensors import _LIMITS_CACHE, _load_resources

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE_URL = "http://localhost:8000"
EVENT_DURATION_SECONDS = 60  # How long the event should last
READINGS_PER_SECOND = 0.2  # How many readings to send per second

app = typer.Typer(
    help="Trigger scripted pollution events at specific monitoring stations.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _find_station(station_id: str, stations: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Find a station's data by its ID."""
    sid = station_id.upper()
    for s in stations:
        if s["station_id"].upper() == sid:
            return s
    return None


def _generate_event_value(pollutant: str, severity: str) -> float:
    """Generate a value based on the event severity."""
    limit = _LIMITS_CACHE.get(pollutant)
    if not limit:
        return 0.0  # Cannot generate event for pollutant without a limit

    if severity == "warning":
        # 1.1x to 1.5x the limit
        value = limit * random.uniform(1.1, 1.5)
    elif severity == "critical":
        # 2x to 4x the limit
        value = limit * random.uniform(2.0, 4.0)
    else: # normal
        # 0.2x to 0.6x the limit
        value = limit * random.uniform(0.2, 0.6)

    return round(max(0.0, value), 4)


async def _run_event(
    station: Dict[str, Any],
    pollutant: str,
    severity: str,
    client: httpx.AsyncClient,
) -> None:
    """Send a stream of readings for the duration of the event."""
    print(
        f"Triggering '{severity}' event for pollutant '{pollutant}' "
        f"at station '{station['station_id']}' for {EVENT_DURATION_SECONDS}s..."
    )
    end_time = datetime.now() + timedelta(seconds=EVENT_DURATION_SECONDS)
    interval = 1.0 / READINGS_PER_SECOND

    while datetime.now() < end_time:
        now = datetime.now(timezone.utc).isoformat()
        value = _generate_event_value(pollutant, severity)
        
        # Find the correct sensor unit
        unit = "unknown"
        for s in station.get("sensors", []):
            if s["pollutant_type"].upper() == pollutant:
                unit = s["unit"]
                break

        reading = {
            "station_id": station["station_id"],
            "pollutant_type": pollutant,
            "value": value,
            "unit": unit,
            "timestamp": now,
            "location_lat": station.get("latitude"),
            "location_lon": station.get("longitude"),
        }

        try:
            resp = await client.post(f"{API_BASE_URL}/api/monitoring/readings", json=reading)
            if resp.status_code != 201:
                print(f"  Error: API returned {resp.status_code}", file=sys.stderr)
        except httpx.RequestError as e:
            print(f"  Error: Request failed: {e}", file=sys.stderr)

        await asyncio.sleep(interval)

    print(f"Event finished for station '{station['station_id']}'.")


@app.command()
def trigger(
    station_id: str = typer.Argument(..., help="The ID of the station (e.g., 'VAPI-001')."),
    pollutant: str = typer.Argument(..., help="The pollutant to trigger (e.g., 'PM25')."),
    severity: str = typer.Option(
        "critical",
        "--severity",
        "-s",
        help="Severity of the event: 'normal', 'warning', or 'critical'.",
        case_sensitive=False,
    ),
) -> None:
    """
    Trigger a pollution event for a specific station and pollutant.
    """
    # Load resources
    stations = _load_resources()
    station = _find_station(station_id, stations)
    ptype = pollutant.upper()

    if not station:
        print(f"Error: Station '{station_id}' not found in 'sample_sensors.json'.")
        raise typer.Exit(code=1)

    if ptype not in _LIMITS_CACHE:
        print(f"Error: Pollutant '{ptype}' not found in 'pollutant_limits.json'.")
        print(f"Available pollutants with limits: {', '.join(_LIMITS_CACHE.keys())}")
        raise typer.Exit(code=1)
        
    if severity not in ["normal", "warning", "critical"]:
        print("Error: Severity must be one of 'normal', 'warning', or 'critical'.")
        raise typer.Exit(code=1)

    async def main():
        async with httpx.AsyncClient() as client:
            await _run_event(station, ptype, severity, client)
    asyncio.run(main())


if __name__ == "__main__":
    app()