"""Step 1 persistence tests — violation & alert persistence, idempotency,
API retrieval, engine recreation, and failure resilience.

Uses an isolated throwaway PostgreSQL database (ecoguardai_step1_test) so the
development/production database is never touched. The whole module skips when
PostgreSQL is not reachable.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import core.database as core_db
from core.database import Alert as AlertORM
from core.database import Violation as ViolationORM
from services import persistence as pers
from services.persistence import (
    generate_event_id,
    handle_violation_detected,
    parse_iso_timestamp,
    persist_alert,
    persist_alerts_for_dispatch,
    persist_violation,
)

TEST_DB_NAME = "ecoguardai_step1_test"
TEST_SCHEMA = "step1_test"
BASE_URL = "postgresql://ecoguard:ecoguard@localhost:5432"


def _violation_payload(**overrides) -> dict:
    payload = {
        "station_id": "TEST-STATION-01",
        "pollutant_type": "SO2",
        "measured_value": 250.0,
        "limit_value": 80.0,
        "unit": "µg/m³",
        "severity": "HIGH",
        "timestamp": "2026-08-23T10:00:00Z",
        "report_text": "Granite-generated violation report body.",
    }
    payload.update(overrides)
    return payload


async def _check_pg() -> bool:
    conn = await asyncpg.connect(BASE_URL + "/postgres")
    await conn.close()
    return True


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def db():
    """Create/drop an isolated SCHEMA in the local dev database (the
    'ecoguard' role cannot CREATE DATABASE); yield (engine, sessionmaker).
    The whole module skips when PostgreSQL is not reachable."""
    try:
        await _check_pg()
    except Exception:
        pytest.skip("PostgreSQL not available for persistence tests")

    url = BASE_URL.replace("postgresql://", "postgresql+asyncpg://") + "/ecoguardai"
    engine = create_async_engine(
        url,
        connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
    )

    async def _setup():
        conn = await asyncpg.connect(BASE_URL + "/ecoguardai")
        await conn.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        await conn.execute(f'CREATE SCHEMA "{TEST_SCHEMA}"')
        await conn.close()
        async with engine.begin() as c:
            await c.run_sync(core_db.Base.metadata.create_all)
            # Partial unique index from migration 0002 (create_all does not
            # create partial indexes).
            await c.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_violations_event_id_unique "
                f'ON "{TEST_SCHEMA}".violations (event_id) WHERE event_id IS NOT NULL'
            )

    await _setup()
    # Dispose pooled connections made on the fixture's temporary event loop so
    # each test's own loop creates fresh ones.
    factory = async_sessionmaker(engine, expire_on_commit=False)

    yield engine, factory

    async def _teardown():
        await engine.dispose()
        conn = await asyncpg.connect(BASE_URL + "/ecoguardai")
        await conn.execute(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE')
        await conn.close()

    await _teardown()


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def _reset_cache(db):
    pers.reset_cache()
    # Clean tables so each test starts from an empty state.
    _, factory = db

    async def _clean():
        async with factory() as s:
            await s.execute(AlertORM.__table__.delete())
            await s.execute(ViolationORM.__table__.delete())
            await s.commit()

    try:
        await _clean()
    except Exception:
        pass

    yield

    pers.reset_cache()
    # The module-scoped `db` fixture is now responsible for all engine disposal.
    # This fixture just cleans tables.


@pytest.fixture()
def factory(db):
    _, f = db
    return f


# ── Event ID ──────────────────────────────────────────────────────────────────

class TestEventId:
    def test_deterministic(self):
        a = generate_event_id("S1", "SO2", "2026-08-23T10:00:00Z", 250.0)
        b = generate_event_id("S1", "SO2", "2026-08-23T10:00:00Z", 250.0)
        assert a == b and len(a) == 64

    def test_equivalent_timestamp_forms_match(self):
        a = generate_event_id("S1", "SO2", "2026-08-23T10:00:00Z", 250.0)
        b = generate_event_id("S1", "SO2", "2026-08-23T12:00:00+02:00", 250.0)
        assert a == b

    def test_different_values_differ(self):
        a = generate_event_id("S1", "SO2", "2026-08-23T10:00:00Z", 250.0)
        b = generate_event_id("S1", "SO2", "2026-08-23T10:00:00Z", 251.0)
        assert a != b

    def test_iso_timestamp_with_nanos_parses(self):
        dt = parse_iso_timestamp("2026-08-23T10:00:00.123456789Z")
        assert dt is not None and dt.microsecond == 123456


# ── Violation persistence ─────────────────────────────────────────────────────

class TestViolationPersistence:
    @pytest.mark.asyncio(loop_scope="module")
    async def test_persist_creates_row(self, factory):
        vid = await persist_violation(_violation_payload(), factory)
        assert vid is not None
        async with factory() as s:
            row = (await s.execute(
                select(ViolationORM).where(ViolationORM.id == vid)
            )).scalar_one()
            assert row.station_id == "TEST-STATION-01"
            assert row.status == "OPEN"
            assert row.report_text == "Granite-generated violation report body."
            assert row.severity == "HIGH"

    @pytest.mark.asyncio(loop_scope="module")
    async def test_duplicate_prevented(self, factory):
        payload = _violation_payload()
        id1 = await persist_violation(payload, factory)
        id2 = await persist_violation(dict(payload), factory)  # fresh dict, same identity
        assert id1 == id2
        async with factory() as s:
            count = (await s.execute(
                select(func.count()).select_from(ViolationORM)
            )).scalar_one()
        assert count == 1

    @pytest.mark.asyncio(loop_scope="module")
    async def test_concurrent_duplicate_calls(self, factory):
        payload = _violation_payload()
        ids = await asyncio.gather(*[
            persist_violation(dict(payload), factory) for _ in range(5)
        ])
        assert len(set(ids)) == 1
        async with factory() as s:
            count = (await s.execute(
                select(func.count()).select_from(ViolationORM)
            )).scalar_one()
        assert count == 1

    @pytest.mark.asyncio(loop_scope="module")
    async def test_orchestrator_handler_fail_safe(self):
        """handle_violation_detected never raises even with a broken session."""
        broken = MagicMock(side_effect=RuntimeError("boom"))
        with patch("services.persistence.AsyncSessionLocal", broken):
            result = await handle_violation_detected(
                _violation_payload(station_id="FAIL-01")
            )
        assert result is None


# ── Alert persistence ─────────────────────────────────────────────────────────

class TestAlertPersistence:
    @pytest.mark.asyncio(loop_scope="module")
    async def test_alert_references_existing_violation(self, factory):
        vid = await persist_violation(_violation_payload(), factory)
        aid = await persist_alert(
            violation_id=vid, alert_type="EMAIL",
            recipient="alerts@ecoguardai.com",
            message="Granite report body", status="SENT",
            sent_at=datetime.now(timezone.utc),
            metadata={"severity": "HIGH"},
            session_factory=factory,
        )
        assert aid is not None
        async with factory() as s:
            alert = (await s.execute(
                select(AlertORM).where(AlertORM.id == aid)
            )).scalar_one()
            assert alert.violation_id == vid
            assert alert.alert_type == "EMAIL"
            assert alert.status == "SENT"
            assert alert.sent_at is not None

    @pytest.mark.asyncio(loop_scope="module")
    async def test_no_alert_without_violation(self, factory):
        """If violation persistence fails, NO invalid alert may be created."""
        from config.settings import settings
        broken = MagicMock(side_effect=RuntimeError("db down"))
        await persist_alerts_for_dispatch(
            _violation_payload(station_id="NO-DB-01"),
            [("EMAIL", settings.alert_email_user, True)],
            session_factory=broken,
        )  # must not raise; nothing persisted
        async with factory() as s:
            count = (await s.execute(
                select(func.count()).select_from(AlertORM)
            )).scalar_one()
        assert count == 0

    @pytest.mark.asyncio(loop_scope="module")
    async def test_dispatch_hook_end_to_end_and_dedup(self, factory):
        payload = _violation_payload(station_id="HOOK-01", severity="CRITICAL")
        dispatched = [
            ("EMAIL", "alerts@ecoguardai.com", True),
            ("WEBHOOK", "https://example.com/webhook", False),
        ]
        await persist_alerts_for_dispatch(payload, dispatched, session_factory=factory)
        async with factory() as s:
            alerts = (await s.execute(select(AlertORM))).scalars().all()
            types = {a.alert_type: a.status for a in alerts}
        assert types == {"EMAIL": "SENT", "WEBHOOK": "FAILED"}

        # Re-running dispatch (cache cleared) must NOT duplicate alert rows.
        pers.reset_cache()
        await persist_alerts_for_dispatch(payload, dispatched, session_factory=factory)
        async with factory() as s:
            n = len((await s.execute(select(AlertORM))).scalars().all())
        assert n == 2


# ── API retrieval ─────────────────────────────────────────────────────────────

class TestApiRetrieval:
    @pytest.mark.asyncio(loop_scope="module")
    async def test_api_returns_persisted_records(self, db, factory):
        """GET /api/violations and /api/alerts return persisted rows via the
        real routes, with the session pointed at the isolated test DB."""
        from config.settings import settings

        async def seed():
            payload = _violation_payload(station_id="API-01", severity="CRITICAL")
            vid = await persist_violation(payload, factory)
            await persist_alerts_for_dispatch(
                payload,
                [
                    ("EMAIL", settings.alert_email_user, True),
                    ("WEBHOOK", settings.regulatory_webhook_url, True),
                ],
                session_factory=factory,
            )
            return vid

        vid = await seed()
        # Drop connections created on the seeding loop before the TestClient
        # (which runs the app on its own event loop) touches the DB.
        await db[0].dispose()

        with (
            patch.object(core_db, "AsyncSessionLocal", factory),
            patch("core.orchestrator.AgentOrchestrator.start", new_callable=AsyncMock),
            patch("core.orchestrator.AgentOrchestrator.stop", new_callable=AsyncMock),
            patch("core.database.init_db", new_callable=AsyncMock),
            patch("core.ibm_cloud.IBMCloudClient.check_watsonx_health",
                  new_callable=AsyncMock, return_value={"status": "healthy"}),
        ):
            from api.main import app
            with TestClient(app, raise_server_exceptions=False) as client:
                vresp = client.get("/api/violations")
                aresp = client.get("/api/alerts")

        assert vresp.status_code == 200
        violations = [v for v in vresp.json() if v["station_id"] == "API-01"]
        assert len(violations) == 1
        v = violations[0]
        assert v["id"] == vid
        assert v["severity"] == "CRITICAL"
        assert v["report_text"] == "Granite-generated violation report body."

        assert aresp.status_code == 200
        alerts = [a for a in aresp.json() if a["violation_id"] == vid]
        assert {a["alert_type"] for a in alerts} == {"EMAIL", "WEBHOOK"}
        assert all(a["status"] == "SENT" for a in alerts)


# ── Engine / session recreation (durability) ──────────────────────────────────

class TestEngineRecreation:
    @pytest.mark.asyncio(loop_scope="module")
    async def test_data_survives_engine_recreation(self, db, factory):
        engine, _ = db

        async def run():
            vid = await persist_violation(
                _violation_payload(station_id="DUR-01"), factory)
            # Simulate process restart: dispose engine, build a fresh one.
            await engine.dispose()
            fresh = create_async_engine(
                engine.url.render_as_string(hide_password=False),
                connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
            )
            fresh_factory = async_sessionmaker(fresh, expire_on_commit=False)
            try:
                # Duplicate call through NEW engine → still exactly one row.
                vid2 = await persist_violation(
                    _violation_payload(station_id="DUR-01"), fresh_factory)
                async with fresh_factory() as s:
                    count = (await s.execute(
                        select(func.count()).select_from(ViolationORM)
                        .where(ViolationORM.station_id == "DUR-01")
                    )).scalar_one()
                    row = (await s.execute(
                        select(ViolationORM)
                        .where(ViolationORM.station_id == "DUR-01")
                    )).scalar_one()
                assert vid == vid2 == row.id
                assert count == 1
                assert row.report_text == "Granite-generated violation report body."
            finally:
                await fresh.dispose()

        await run()


# ── Pipeline failure resilience ───────────────────────────────────────────────

class TestFailureResilience:
    @pytest.mark.asyncio(loop_scope="module")
    async def test_all_entry_points_swallow_db_failure(self):
        broken = MagicMock(side_effect=RuntimeError("connection refused"))
        assert await persist_violation(_violation_payload(), broken) is None
        assert await persist_alert(
            1, "EMAIL", "x@y.z", "m", session_factory=broken) is None
        # Hook must not raise either.
        await persist_alerts_for_dispatch(
            _violation_payload(station_id="RESIL-02"),
            [("DASHBOARD", "dashboard", True)],
            session_factory=broken,
        )

    @pytest.mark.asyncio(loop_scope="module")
    async def test_pipeline_continues_after_persistence_failure(self):
        """RegulatoryAlertAgent.run completes even when persistence fails."""
        from agents.alert_agent import RegulatoryAlertAgent

        agent = RegulatoryAlertAgent.__new__(RegulatoryAlertAgent)
        agent._last_alert = {}
        agent._recipients = ["alerts@ecoguardai.com"]

        class FakeOrch:
            async def emit(self, *a, **k):
                pass

        object.__setattr__(agent, "orchestrator", FakeOrch())
        broken = MagicMock(side_effect=RuntimeError("no db"))
        with patch("services.persistence.AsyncSessionLocal", broken):
            result = await agent.run(_violation_payload(severity="HIGH"))
        assert result["status"] == "dispatched"