"""Sensor data simulator — posts realistic readings to the EcoGuardAI API."""
from __future__ import annotations

import json
import math
import random
import sys
import time
import os
import sys
if sys.platform.startswith('win'):
    import msvcrt
else:
    import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE_URL = "http://localhost:8000"
INTERVAL_SECONDS = 5          # delay between batches
VIOLATION_PROBABILITY = 0.10  # 10% chance a reading exceeds the limit
MAX_RETRIES = 3               # max retries for 429 rate limits
RETRY_BACKOFF = 2             # exponential backoff multiplier

STATIONS_FILE = Path(__file__).parent.parent / "data" / "sample_sensors.json"
LIMITS_FILE = Path(__file__).parent.parent / "data" / "pollutant_limits.json"
PID_FILE = Path(__file__).parent.parent / ".simulator.pid"

# Typical baseline readings (pollutant → (mean, std_dev))
_BASELINES: Dict[str, tuple] = {
    "SO2":       (35.0, 15.0),
    "NOX":       (40.0, 18.0),
    "PM25":      (30.0, 12.0),
    "PM10":      (55.0, 20.0),
    "CO":        (800.0, 200.0),
    "BOD":       (12.0, 6.0),
    "COD":       (80.0, 30.0),
    "TSS":       (40.0, 15.0),
    "PH":        (7.2, 0.4),
    "TDS":       (600.0, 150.0),
    "PB":        (0.01, 0.005),
    "CR":        (0.03, 0.015),
    "CR6":       (0.02, 0.01),
    "HG":        (0.0003, 0.0002),
    "AS":        (0.01, 0.005),
    "CD":        (0.003, 0.002),
    "NI":        (0.05, 0.025),
    "ZN":        (0.8, 0.4),
    "CU":        (0.4, 0.2),
    "FE":        (0.9, 0.45),
    "NH3":       (15.0, 7.0),
    "H2S":       (1.5, 0.8),
    "FLUORIDE":  (0.8, 0.3),
    "HCL":       (10.0, 5.0),
    "CL2":       (5.0, 2.5),
    "O3":        (25.0, 12.0),
    "BENZENE":   (1.5, 0.8),
    # CETP-specific
    "OIL":       (5.0, 2.5),
    "FLOW":      (5000.0, 1500.0),
}

_LIMITS_CACHE: Dict[str, float] = {}


def _load_resources() -> List[Dict[str, Any]]:
    with STATIONS_FILE.open() as f:
        stations = json.load(f)
    with LIMITS_FILE.open() as f:
        limits_data = json.load(f)

    for category in ("air", "water", "heavy_metals", "cetp"):
        for ptype, info in limits_data.get(category, {}).items():
            if isinstance(info, dict):
                _LIMITS_CACHE[ptype] = float(info.get("limit") or info.get("max", 1e9))
            else:
                _LIMITS_CACHE[ptype] = float(info)
    return stations


def _simulate_value(pollutant: str, violate: bool, industry_type: str) -> float:
    """Generate a realistic sensor value, optionally above the regulatory limit."""
    mean, std = _BASELINES.get(pollutant, (50.0, 20.0))
    value = random.gauss(mean, std)

    if violate:
        limit = _LIMITS_CACHE.get(pollutant)
        if limit:
            # Set value 10–100% above the limit
            excess_pct = random.uniform(0.1, 1.0)
            value = limit * (1.0 + excess_pct)

    # Special handling for certain pollutants
    if pollutant == "PH":
        value = max(2.0, min(14.0, value))
    elif pollutant == "FLOW" and industry_type == "cetp":
        # Flow rate for CETP should be high
        value = max(1000.0, value)
    else:
        value = max(0.0, value)

    return round(value, 4)


def _simulate_batch(stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Produce one reading per sensor for every station."""
    readings = []
    now = datetime.now(timezone.utc).isoformat()

    for station in stations:
        violate = random.random() < VIOLATION_PROBABILITY
        for sensor in station.get("sensors", []):
            ptype = sensor["pollutant_type"].upper()
            value = _simulate_value(ptype, violate, station.get("industry_type", ""))
            readings.append({
                "station_id": station["station_id"],
                "pollutant_type": ptype,
                "value": value,
                "unit": sensor["unit"],
                "timestamp": now,
                "location_lat": station.get("latitude"),
                "location_lon": station.get("longitude"),
            })
    return readings


def post_readings(readings: List[Dict[str, Any]], client: httpx.Client) -> None:
    """Post a batch of readings to the API with retries for 429 rate limits."""
    if not readings:
        return

    payload = {"readings": readings, "source": "simulator"}
    success_count = 0
    error_count = 0
    retries = 0
    backoff = 1  # initial backoff in seconds
    
    while retries < MAX_RETRIES:
        try:
            resp = client.post(
                f"{API_BASE_URL}/api/monitoring/readings/bulk",
                json=payload,
                timeout=15,
            )
            if resp.status_code == 201:
                success_count = resp.json().get("accepted", 0)
                error_count = 0
                break
            elif resp.status_code == 429:
                # Rate limit hit, retry with backoff
                retries += 1
                print(f"  WARNING: Rate limit exceeded (429), retrying in {backoff}s (attempt {retries}/{MAX_RETRIES})", file=sys.stderr)
                time.sleep(backoff)
                backoff *= RETRY_BACKOFF  # exponential backoff
            else:
                error_count = len(readings)
                print(f"  WARNING: Bulk POST failed {resp.status_code}: {resp.text[:100]}", file=sys.stderr)
                break

        except httpx.RequestError as exc:
            error_count = len(readings)
            print(f"  WARNING: Connection error: {exc}", file=sys.stderr)
            break

    if retries >= MAX_RETRIES and error_count == 0:
        error_count = len(readings)
        print(f"  ERROR: Max retries exceeded, failed to post batch after {MAX_RETRIES} attempts", file=sys.stderr)

    print(
        f"[{datetime.now().strftime('%H:%M:%S')}] "
        f"Posted {len(readings)} readings | OK {success_count} FAILED {error_count}"
    )


def main() -> None:
    # Acquire PID file lock to prevent multiple simulator instances (cross-platform)
    pid_file = None
    try:
        # Create PID file if it doesn't exist
        if PID_FILE.exists():
            # Try to read existing PID to check if it's still running
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())
                # Check if process is still alive
                if sys.platform.startswith('win'):
                    # Windows: check if process exists using tasklist
                    import subprocess
                    result = subprocess.run(['tasklist', '/FI', f'PID eq {old_pid}'], capture_output=True, text=True)
                    if str(old_pid) in result.stdout:
                        print("ERROR: Another simulator instance is already running (PID file locked). Exiting.", file=sys.stderr)
                        sys.exit(1)
                else:
                    # Unix-like: send signal 0 to check if process exists
                    os.kill(old_pid, 0)
                    # If no exception, process is still running
                    print("ERROR: Another simulator instance is already running (PID file locked). Exiting.", file=sys.stderr)
                    sys.exit(1)
            except (OSError, ValueError):
                # Old PID doesn't exist or is invalid, we can take over
                pass
        # Open and lock the PID file
        pid_file = open(PID_FILE, 'w')
        if sys.platform.startswith('win'):
            # Windows file locking
            msvcrt.locking(pid_file.fileno(), msvcrt.LK_NBLCK, 1024)
        else:
            # Unix file locking
            fcntl.flock(pid_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write current PID to file
        pid_file.write(str(os.getpid()))
        pid_file.flush()
    except BlockingIOError:
        print("ERROR: Another simulator instance is already running (PID file locked). Exiting.", file=sys.stderr)
        if pid_file:
            pid_file.close()
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to acquire PID file lock: {e}", file=sys.stderr)
        if pid_file:
            pid_file.close()
        sys.exit(1)

    # Cleanup PID file on exit
    import atexit
    def cleanup():
        try:
            if pid_file:
                if sys.platform.startswith('win'):
                    msvcrt.locking(pid_file.fileno(), msvcrt.LK_UNLCK, 1024)
                else:
                    fcntl.flock(pid_file, fcntl.LOCK_UN)
                pid_file.close()
            if PID_FILE.exists():
                PID_FILE.unlink()
        except:
            pass
    atexit.register(cleanup)

    stations = _load_resources()
    print(
        f"EcoGuardAI Sensor Simulator\n"
        f"  Stations : {len(stations)}\n"
        f"  API URL  : {API_BASE_URL}\n"
        f"  Interval : {INTERVAL_SECONDS}s\n"
        f"  Violation probability: {VIOLATION_PROBABILITY*100:.0f}%\n"
        f"  Press Ctrl+C to stop.\n"
    )

    with httpx.Client() as client:
        while True:
            batch = _simulate_batch(stations)
            post_readings(batch, client)
            time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()