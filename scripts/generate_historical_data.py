"""Generate historical sensor data for a specified number of days."""
from __future__ import annotations

import asyncio
import json
import random
import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PID_FILE = Path(__file__).parent.parent / ".historical_generator.pid"
MAX_RETRIES = 3
RETRY_BACKOFF = 2

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE_URL = "http://localhost:8000"
NUM_DAYS = 30  # Number of past days to generate data for
READINGS_PER_HOUR = 4  # Number of readings to generate per hour

STATIONS_FILE = Path(__file__).parent.parent / "data" / "sample_sensors.json"
LIMITS_FILE = Path(__file__).parent.parent / "data" / "pollutant_limits.json"

# Use the same baselines and simulation logic as the real-time simulator
sys.path.append(str(Path(__file__).parent))
from simulate_sensors import _BASELINES, _LIMITS_CACHE, _load_resources, _simulate_value


async def generate_and_post_historical_data() -> None:
    """Generate and post historical data for all stations."""
    stations = _load_resources()
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=NUM_DAYS)

    print(f"Generating historical data from {start_time.isoformat()} to {end_time.isoformat()}...")

    all_readings = []
    current_time = start_time
    while current_time < end_time:
        for station in stations:
            # 1% chance of a violation event starting at this interval
            violate = random.random() < 0.01
            for sensor in station.get("sensors", []):
                ptype = sensor["pollutant_type"].upper()
                value = _simulate_value(ptype, violate, station.get("industry_type", ""))
                all_readings.append({
                    "station_id": station["station_id"],
                    "pollutant_type": ptype,
                    "value": value,
                    "unit": sensor["unit"],
                    "timestamp": current_time.isoformat(),
                    "location_lat": station.get("latitude"),
                    "location_lon": station.get("longitude"),
                })
        current_time += timedelta(hours=1.0 / READINGS_PER_HOUR)

    print(f"Generated {len(all_readings)} total historical readings.")
    print("Posting to API in chunks...")

    async with httpx.AsyncClient() as client:
        chunk_size = 500
        for i in range(0, len(all_readings), chunk_size):
            chunk = all_readings[i:i + chunk_size]
            retries = 0
            backoff = 1
            success = False
            while retries < MAX_RETRIES:
                try:
                    # Use the bulk endpoint for efficiency
                    resp = await client.post(
                        f"{API_BASE_URL}/api/monitoring/readings/bulk",
                        json={"readings": chunk},
                        timeout=30,
                    )
                    if resp.status_code == 201:
                        print(f"  Posted chunk {i//chunk_size + 1}/{(len(all_readings)//chunk_size)+1} successfully.")
                        success = True
                        break
                    elif resp.status_code == 429:
                        retries += 1
                        print(f"  WARNING: Rate limit exceeded (429) on chunk {i//chunk_size +1}, retrying in {backoff}s (attempt {retries}/{MAX_RETRIES})")
                        await asyncio.sleep(backoff)
                        backoff *= RETRY_BACKOFF
                    else:
                        print(f"  ERROR posting chunk: {resp.status_code} {resp.text[:100]}")
                        break
                except httpx.RequestError as e:
                    print(f"  ERROR during request: {e}")
                    break
            if not success and retries >= MAX_RETRIES:
                print(f"  ERROR: Max retries exceeded for chunk {i//chunk_size +1}, failed to post.")
            await asyncio.sleep(0.5) # Avoid overwhelming the API

    print("Historical data generation complete.")


def main() -> None:
    # PID file locking to prevent multiple instances
    pid_file = None
    try:
        if PID_FILE.exists():
            try:
                with open(PID_FILE, 'r') as f:
                    old_pid = int(f.read().strip())
                # Check if process is still running
                if sys.platform.startswith('win'):
                    result = subprocess.run(['tasklist', '/FI', f'PID eq {old_pid}'], capture_output=True, text=True)
                    if str(old_pid) in result.stdout:
                        print("ERROR: Another historical data generator instance is already running. Exiting.", file=sys.stderr)
                        sys.exit(1)
                else:
                    os.kill(old_pid, 0)
                    print("ERROR: Another historical data generator instance is already running. Exiting.", file=sys.stderr)
                    sys.exit(1)
            except (OSError, ValueError):
                pass  # Old PID is invalid, proceed
        # Create and lock PID file
        pid_file = open(PID_FILE, 'w')
        if sys.platform.startswith('win'):
            import msvcrt
            msvcrt.locking(pid_file.fileno(), msvcrt.LK_NBLCK, 1024)
        else:
            import fcntl
            fcntl.flock(pid_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pid_file.write(str(os.getpid()))
        pid_file.flush()
    except BlockingIOError:
        print("ERROR: Another historical data generator instance is already running. Exiting.", file=sys.stderr)
        if pid_file:
            pid_file.close()
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Failed to acquire PID lock: {e}", file=sys.stderr)
        if pid_file:
            pid_file.close()
        sys.exit(1)

    # Cleanup PID file
    import atexit
    def cleanup():
        try:
            if pid_file:
                if sys.platform.startswith('win'):
                    import msvcrt
                    msvcrt.locking(pid_file.fileno(), msvcrt.LK_UNLCK, 1024)
                else:
                    import fcntl
                    fcntl.flock(pid_file, fcntl.LOCK_UN)
                pid_file.close()
            if PID_FILE.exists():
                PID_FILE.unlink()
        except:
            pass
    atexit.register(cleanup)

    # Add the parent directory to the path to allow importing simulate_sensors
    sys.path.append(str(Path(__file__).parent.parent))
    asyncio.run(generate_and_post_historical_data())


if __name__ == "__main__":
    main()