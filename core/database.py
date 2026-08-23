"""SQLAlchemy async ORM models and database session management."""
from __future__ import annotations

from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

from config.settings import settings
from loguru import logger


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# ── ORM Models ────────────────────────────────────────────────────────────────

class IndustrialUnit(Base):
    """Represents a permitted industrial unit in the Golden Corridor."""

    __tablename__ = "industrial_units"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    location = Column(String(255))
    industry_type = Column(String(100))
    latitude = Column(Float)
    longitude = Column(Float)
    permitted_limits = Column(JSONB, default=dict)
    population_data = Column(JSONB, default=dict)
    contact_email = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    readings = relationship("SensorReading", back_populates="station", lazy="select")
    violations = relationship(
        "Violation",
        primaryjoin="foreign(Violation.station_id) == IndustrialUnit.name",
        viewonly=True,
        lazy="select",
    )


class SensorReading(Base):
    """Time-series sensor reading from an industrial monitoring station."""

    __tablename__ = "sensor_readings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    station_id = Column(String(50), ForeignKey("industrial_units.name", ondelete="SET NULL"), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    pollutant_type = Column(String(50), nullable=False, index=True)
    value = Column(Float, nullable=False)
    unit = Column(String(30), nullable=False)
    location_lat = Column(Float)
    location_lon = Column(Float)
    sensor_metadata = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    station = relationship("IndustrialUnit", back_populates="readings", foreign_keys=[station_id])

    def to_dict(self) -> dict:
        """Return a dictionary representation for API/agent emission."""
        return {
            "station_id": self.station_id,
            "pollutant_type": self.pollutant_type,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "location_lat": self.location_lat,
            "location_lon": self.location_lon,
            "sensor_metadata": self.sensor_metadata or {},
        }


class Violation(Base):
    """A recorded regulatory violation at an industrial monitoring station."""

    __tablename__ = "violations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    station_id = Column(String(50), nullable=False, index=True)
    pollutant_type = Column(String(50), nullable=False)
    measured_value = Column(Float, nullable=False)
    limit_value = Column(Float, nullable=False)
    unit = Column(String(30))
    severity = Column(String(20), nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    status = Column(String(30), default="OPEN")
    report_text = Column(Text)
    notes = Column(Text)
    # Step 1 (persistence): deterministic deduplication key. Nullable so that
    # pre-existing rows and external inserts remain valid.
    event_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    station_rel = relationship(
        "IndustrialUnit",
        primaryjoin="foreign(Violation.station_id) == IndustrialUnit.name",
        foreign_keys=[station_id],
        viewonly=True,
    )
    alerts = relationship("Alert", back_populates="violation", lazy="select")


class Alert(Base):
    """An alert dispatched in response to a violation."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    violation_id = Column(Integer, ForeignKey("violations.id", ondelete="CASCADE"), nullable=False, index=True)
    alert_type = Column(String(30), nullable=False)
    recipient = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(30), default="PENDING")
    metadata_ = Column("metadata", JSONB, default=dict)
    sent_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    violation = relationship("Violation", back_populates="alerts")


# ── Engine & Session ──────────────────────────────────────────────────────────

_engine = create_async_engine(
    settings.async_database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create all tables if they don't exist.

    Development convenience: uses SQLAlchemy's create_all() for rapid
    local spin-up without needing a migration history.

    PRODUCTION NOTE: Do NOT rely on this for production deployments.
    Instead, run database migrations before starting the application:

        alembic upgrade head

    This ensures schema changes are versioned and rollback-capable.
    create_all() is kept here so that the test suite and local Docker
    Compose workflow continue to work without Alembic installed or a
    migration history present.
    """
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema initialised.")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def shutdown_db() -> None:
    """Dispose of the database engine connection pool."""
    logger.info("Disposing of database engine connection pool...")
    await _engine.dispose()
    logger.info("Database engine connection pool disposed.")