"""Initial schema — all four EcoGuardAI tables.

Hand-written from core/database.py ORM models (does not require a live
database connection to generate).  Review this file against the ORM before
running `alembic upgrade head`.

Tables created:
  industrial_units   — permitted industrial units in the Golden Corridor
  sensor_readings    — time-series pollutant readings
  violations         — regulatory threshold exceedances
  alerts             — dispatched alert records

Revision: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

# revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── industrial_units ──────────────────────────────────────────────────────
    op.create_table(
        "industrial_units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("industry_type", sa.String(100), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("permitted_limits", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── sensor_readings ───────────────────────────────────────────────────────
    op.create_table(
        "sensor_readings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("station_id", sa.String(50), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("pollutant_type", sa.String(50), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(30), nullable=False),
        sa.Column("location_lat", sa.Float(), nullable=True),
        sa.Column("location_lon", sa.Float(), nullable=True),
        sa.Column("sensor_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["industrial_units.name"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sensor_readings_station_id"),
        "sensor_readings",
        ["station_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sensor_readings_timestamp"),
        "sensor_readings",
        ["timestamp"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sensor_readings_pollutant_type"),
        "sensor_readings",
        ["pollutant_type"],
        unique=False,
    )

    # ── violations ────────────────────────────────────────────────────────────
    op.create_table(
        "violations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("station_id", sa.String(50), nullable=False),
        sa.Column("pollutant_type", sa.String(50), nullable=False),
        sa.Column("measured_value", sa.Float(), nullable=False),
        sa.Column("limit_value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(30), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("report_text", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_violations_station_id"),
        "violations",
        ["station_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_violations_timestamp"),
        "violations",
        ["timestamp"],
        unique=False,
    )

    # ── alerts ────────────────────────────────────────────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("violation_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(30), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["violation_id"],
            ["violations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_alerts_violation_id"),
        "alerts",
        ["violation_id"],
        unique=False,
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index(op.f("ix_alerts_violation_id"), table_name="alerts")
    op.drop_table("alerts")

    op.drop_index(op.f("ix_violations_timestamp"), table_name="violations")
    op.drop_index(op.f("ix_violations_station_id"), table_name="violations")
    op.drop_table("violations")

    op.drop_index(op.f("ix_sensor_readings_pollutant_type"), table_name="sensor_readings")
    op.drop_index(op.f("ix_sensor_readings_timestamp"), table_name="sensor_readings")
    op.drop_index(op.f("ix_sensor_readings_station_id"), table_name="sensor_readings")
    op.drop_table("sensor_readings")

    op.drop_table("industrial_units")
