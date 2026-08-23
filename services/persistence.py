"""Fail-safe persistence service for violations and alerts (Step 1).

Design guarantees:
- Persistence NEVER crashes the pipeline. Every public function catches all
  exceptions, logs, and returns ``None`` on failure.
- Violation persistence is idempotent: a deterministic SHA-256 ``event_id``
  derived from (station_id, pollutant_type, timestamp, measured_value) plus a
  PostgreSQL partial unique index + ``ON CONFLICT DO NOTHING`` guarantees at
  most one violation row per event.
- Alerts always reference an existing persisted violation: the violation DB id
  is resolved/created BEFORE the alert row is inserted.
- Suppressed / rate-limited alerts are never passed to this module.

The SSE payload structure and API response schemas are unchanged — event_id is
an internal deduplication key only.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.database import Alert as AlertORM
from core.database import AsyncSessionLocal
from core.database import Violation as ViolationORM


# ── Deterministic event ID ────────────────────────────────────────────────────

def generate_event_id(
    station_id: Any,
    pollutant_type: Any,
    timestamp: Any,
    measured_value: Any,
) -> str:
    """Deterministic SHA-256 hash of the violation identity fields."""
    raw = "|".join(
        str(part)
        for part in (
            station_id,
            pollutant_type,
            _normalise_timestamp(timestamp),
            measured_value,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalise_timestamp(value: Any) -> str:
    """Normalise a timestamp (datetime or ISO string) to UTC ISO format.

    Ensures that equivalent timestamps expressed differently (e.g. 'Z' vs
    '+00:00', or varying sub-second precision) produce identical hashes.
    """
    dt = parse_iso_timestamp(value)
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="microseconds")


def parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp string or pass through a datetime.

    Returns None when unparseable; asyncpg cannot bind ISO strings directly
    so this conversion is required before persistence.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        # Trim fractional seconds beyond microseconds (asyncpg rejects them)
        if "." in text:
            head, tail = text.split(".", 1)
            for i, ch in enumerate(tail):
                if not ch.isdigit():
                    frac, rest = tail[:i], tail[i:]
                    break
            else:
                frac, rest = tail, ""
            frac = (frac + "000000")[:6]
            text = f"{head}.{frac}{rest}"
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        logger.warning("Could not parse violation timestamp: {!r}", value)
        return None


# ── In-process coordination ───────────────────────────────────────────────────
# Guards against concurrent double-persist within one process and caches
# event_id → violation DB id so alert persistence can reuse it.

_violation_locks: Dict[str, asyncio.Lock] = {}
_violation_ids: Dict[str, int] = {}


def _lock_for(event_id: str) -> asyncio.Lock:
    lock = _violation_locks.get(event_id)
    if lock is None:
        lock = asyncio.Lock()
        _violation_locks[event_id] = lock
    return lock


def cached_violation_id(event_id: str) -> Optional[int]:
    """Return the cached DB id for an event_id, if already persisted."""
    return _violation_ids.get(event_id)


def reset_cache() -> None:
    """Clear in-process caches (used by tests)."""
    _violation_locks.clear()
    _violation_ids.clear()


def _session_factory(override: Optional[async_sessionmaker]):
    return override or AsyncSessionLocal


# ── Violation persistence ─────────────────────────────────────────────────────

async def persist_violation(
    violation: Dict[str, Any],
    session_factory: Optional[async_sessionmaker] = None,
) -> Optional[int]:
    """Persist a violation dict from the ``violation.detected`` event.

    Idempotent: repeated calls with the same violation identity return the
    same existing DB row id instead of creating duplicates.

    Fail-safe: returns ``None`` on any failure without raising.
    """
    try:
        station_id = violation.get("station_id", "UNKNOWN")
        pollutant = violation.get("pollutant_type", "UNKNOWN")
        ts_raw = violation.get("timestamp")
        measured = violation.get("measured_value")

        event_id = violation.get("event_id") or generate_event_id(
            station_id, pollutant, ts_raw, measured
        )

        # Fast path: already known in this process.
        existing = _violation_ids.get(event_id)
        if existing is not None:
            return existing

        async with _lock_for(event_id):
            # Double-checked under lock (another coroutine may have won).
            existing = _violation_ids.get(event_id)
            if existing is not None:
                return existing

            values = {
                "event_id": event_id,
                "station_id": str(station_id),
                "pollutant_type": str(pollutant),
                "measured_value": float(measured),
                "limit_value": float(violation.get("limit_value", 1) or 1),
                "unit": str(violation.get("unit") or ""),
                "severity": str(violation.get("severity") or "LOW"),
                "timestamp": parse_iso_timestamp(ts_raw) or datetime.now(timezone.utc),
                "status": "OPEN",
                "report_text": violation.get("report_text"),
                "notes": violation.get("notes"),
            }

            factory = _session_factory(session_factory)
            async with factory() as session:
                # Idempotent insert (PostgreSQL ON CONFLICT DO NOTHING against
                # the PARTIAL unique index created by migration 0002 — the
                # index_where predicate is required for inference).
                stmt = pg_insert(ViolationORM).values(**values)
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["event_id"],
                    index_where=ViolationORM.event_id.is_not(None),
                )
                await session.execute(stmt)
                await session.commit()

                # Recover the canonical row id (ours or the pre-existing one).
                result = await session.execute(
                    select(ViolationORM.id).where(ViolationORM.event_id == event_id)
                )
                db_id = result.scalar_one_or_none()

            if db_id is not None:
                _violation_ids[event_id] = db_id
                logger.info(
                    "Violation persisted | id={} event_id={} station={} pollutant={}",
                    db_id, event_id[:12], station_id, pollutant,
                )
            else:
                logger.warning(
                    "Violation persist could not recover DB id | event_id={}",
                    event_id[:12],
                )
            return db_id
    except Exception as exc:
        logger.error("Violation persistence failed (pipeline continues): {}", exc)
        return None


async def handle_violation_detected(payload: Dict[str, Any]) -> None:
    """Orchestrator event-bus handler for ``violation.detected``.

    Registered alongside (never replacing) the alert / health / dashboard
    handlers. Fully fail-safe by contract of :func:`persist_violation`.
    """
    await persist_violation(payload)


# ── Alert persistence ─────────────────────────────────────────────────────────

async def ensure_violation_persisted(
    violation: Dict[str, Any],
    session_factory: Optional[async_sessionmaker] = None,
) -> Optional[int]:
    """Return the DB id for a violation dict, persisting it if necessary.

    Guarantees alerts only ever reference an existing violation row.
    """
    event_id = violation.get("event_id") or generate_event_id(
        violation.get("station_id"),
        violation.get("pollutant_type"),
        violation.get("timestamp"),
        violation.get("measured_value"),
    )
    cached = _violation_ids.get(event_id)
    if cached is not None:
        return cached
    return await persist_violation(violation, session_factory)


async def persist_alert(
    violation_id: int,
    alert_type: str,
    recipient: str,
    message: str,
    status: str = "SENT",
    sent_at: Optional[datetime] = None,
    metadata: Optional[dict] = None,
    session_factory: Optional[async_sessionmaker] = None,
) -> Optional[int]:
    """Persist one dispatched alert record.

    Duplicate protection: skips insertion when an identical
    (violation_id, alert_type, recipient) alert already exists.

    Fail-safe: returns ``None`` on any failure without raising.
    """
    try:
        factory = _session_factory(session_factory)
        async with factory() as session:
            # Simple safe duplicate check on the existing schema.
            dup = await session.execute(
                select(AlertORM.id)
                .where(AlertORM.violation_id == violation_id)
                .where(AlertORM.alert_type == alert_type)
                .where(AlertORM.recipient == recipient)
                .limit(1)
            )
            existing_id = dup.scalar_one_or_none()
            if existing_id is not None:
                return existing_id

            alert = AlertORM(
                violation_id=violation_id,
                alert_type=alert_type,
                recipient=recipient,
                message=message,
                status=status,
                metadata_=metadata or {},
                sent_at=sent_at,
            )
            session.add(alert)
            await session.commit()
            await session.refresh(alert)

            logger.info(
                "Alert persisted | id={} violation_id={} type={} status={}",
                alert.id, violation_id, alert_type, status,
            )
            return alert.id
    except Exception as exc:
        logger.error("Alert persistence failed (pipeline continues): {}", exc)
        return None


async def persist_alerts_for_dispatch(
    violation: Dict[str, Any],
    dispatched: List[Tuple[str, str, bool]],
    session_factory: Optional[async_sessionmaker] = None,
) -> None:
    """Persistence hook called AFTER RegulatoryAlertAgent dispatch.

    Args:
        violation: The original violation dict (persisted first).
        dispatched: Tuples of ``(alert_type, recipient, success)`` describing
            what was ACTUALLY dispatched. Suppressed / rate-limited channels
            must NOT appear here.

    The violation is guaranteed persisted (DB id recovered) BEFORE any alert
    row is created. If violation persistence fails, no alert rows are created
    (avoiding FK-orphans) and the runtime flow continues unaffected.
    """
    try:
        violation_id = await ensure_violation_persisted(violation, session_factory)
        if violation_id is None:
            logger.error(
                "Skipping alert persistence: violation could not be persisted "
                "(station={}, pollutant={}). Runtime flow unaffected.",
                violation.get("station_id"),
                violation.get("pollutant_type"),
            )
            return

        now = datetime.now(timezone.utc)
        for alert_type, recipient, ok in dispatched:
            await persist_alert(
                violation_id=violation_id,
                alert_type=alert_type,
                recipient=recipient,
                message=violation.get("report_text")
                or f"{violation.get('severity')} violation at {violation.get('station_id')}",
                status="SENT" if ok else "FAILED",
                sent_at=now if ok else None,
                metadata={"severity": violation.get("severity")},
                session_factory=session_factory,
            )
    except Exception as exc:
        logger.error("Alert dispatch persistence hook failed (continuing): {}", exc)