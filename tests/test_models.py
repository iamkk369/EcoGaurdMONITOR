"""Pydantic model validation tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from models.sensor_reading import SensorReadingCreate, BulkSensorReading
from models.violation import ViolationCreate, ViolationSeverity, ViolationStatus
from models.alert import AlertCreate, AlertType, AlertStatus


# ── SensorReadingCreate ────────────────────────────────────────────────────────

class TestSensorReadingCreate:
    def test_valid_reading(self):
        r = SensorReadingCreate(
            station_id="VAPI-001",
            pollutant_type="SO2",
            value=55.0,
            unit="µg/m³",
        )
        assert r.station_id == "VAPI-001"
        assert r.pollutant_type == "SO2"
        assert r.value == 55.0

    def test_pollutant_normalised_to_uppercase(self):
        r = SensorReadingCreate(station_id="X", pollutant_type="pm25", value=30, unit="µg/m³")
        assert r.pollutant_type == "PM25"

    def test_pollutant_dots_removed(self):
        r = SensorReadingCreate(station_id="X", pollutant_type="PM.2.5", value=30, unit="µg/m³")
        assert "." not in r.pollutant_type

    def test_negative_value_raises(self):
        with pytest.raises(ValidationError):
            SensorReadingCreate(station_id="X", pollutant_type="SO2", value=-1, unit="µg/m³")

    def test_excessively_large_value_raises(self):
        with pytest.raises(ValidationError):
            SensorReadingCreate(station_id="X", pollutant_type="SO2", value=2_000_000, unit="µg/m³")

    def test_latitude_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            SensorReadingCreate(
                station_id="X", pollutant_type="SO2", value=50, unit="µg/m³", location_lat=100.0
            )

    def test_longitude_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            SensorReadingCreate(
                station_id="X", pollutant_type="SO2", value=50, unit="µg/m³", location_lon=200.0
            )

    def test_optional_fields_default_none(self):
        r = SensorReadingCreate(station_id="X", pollutant_type="SO2", value=10, unit="µg/m³")
        assert r.location_lat is None
        assert r.location_lon is None
        assert r.sensor_metadata is None

    def test_timestamp_defaults_to_now(self):
        r = SensorReadingCreate(station_id="X", pollutant_type="SO2", value=10, unit="µg/m³")
        assert r.timestamp is not None
        assert isinstance(r.timestamp, datetime)

    def test_value_rounded_to_4_decimals(self):
        r = SensorReadingCreate(station_id="X", pollutant_type="SO2", value=1.123456789, unit="µg/m³")
        assert len(str(r.value).rstrip("0").split(".")[-1]) <= 4

    def test_whitespace_stripped_from_station_id(self):
        r = SensorReadingCreate(station_id="  VAPI-001  ", pollutant_type="SO2", value=10, unit="µg/m³")
        assert r.station_id == "VAPI-001"


# ── BulkSensorReading ─────────────────────────────────────────────────────────

class TestBulkSensorReading:
    def test_valid_bulk(self):
        b = BulkSensorReading(
            readings=[
                {"station_id": "A", "pollutant_type": "SO2", "value": 30, "unit": "µg/m³"},
                {"station_id": "B", "pollutant_type": "PM25", "value": 20, "unit": "µg/m³"},
            ]
        )
        assert len(b.readings) == 2

    def test_empty_readings_raises(self):
        with pytest.raises(ValidationError):
            BulkSensorReading(readings=[])

    def test_source_defaults_to_api(self):
        b = BulkSensorReading(
            readings=[{"station_id": "A", "pollutant_type": "SO2", "value": 30, "unit": "µg/m³"}]
        )
        assert b.source == "api"


# ── ViolationCreate ────────────────────────────────────────────────────────────

class TestViolationCreate:
    def test_valid_violation(self):
        v = ViolationCreate(
            station_id="VAPI-001",
            pollutant_type="SO2",
            measured_value=250.0,
            limit_value=80.0,
            unit="µg/m³",
            severity=ViolationSeverity.HIGH,
        )
        assert v.severity == ViolationSeverity.HIGH

    def test_severity_enum_values(self):
        assert ViolationSeverity.LOW.value == "LOW"
        assert ViolationSeverity.MEDIUM.value == "MEDIUM"
        assert ViolationSeverity.HIGH.value == "HIGH"
        assert ViolationSeverity.CRITICAL.value == "CRITICAL"

    def test_exceedance_ratio_calculated(self):
        v = ViolationCreate(
            station_id="X",
            pollutant_type="SO2",
            measured_value=160.0,
            limit_value=80.0,
            unit="µg/m³",
            severity=ViolationSeverity.HIGH,
        )
        assert v.exceedance_ratio == pytest.approx(2.0)

    def test_invalid_severity_raises(self):
        with pytest.raises(ValidationError):
            ViolationCreate(
                station_id="X",
                pollutant_type="SO2",
                measured_value=100,
                limit_value=80,
                unit="µg/m³",
                severity="EXTREME",  # not a valid enum value
            )

    def test_zero_measured_value_allowed(self):
        v = ViolationCreate(
            station_id="X",
            pollutant_type="SO2",
            measured_value=0.0,
            limit_value=80.0,
            unit="µg/m³",
            severity=ViolationSeverity.LOW,
        )
        assert v.measured_value == 0.0

    def test_negative_limit_raises(self):
        with pytest.raises(ValidationError):
            ViolationCreate(
                station_id="X",
                pollutant_type="SO2",
                measured_value=100,
                limit_value=-1,
                unit="µg/m³",
                severity=ViolationSeverity.HIGH,
            )


# ── AlertCreate ───────────────────────────────────────────────────────────────

class TestAlertCreate:
    def test_valid_email_alert(self):
        a = AlertCreate(
            violation_id=1,
            alert_type=AlertType.EMAIL,
            recipient="compliance@ecoguardai.com",
            message="Test alert message",
        )
        assert a.alert_type == AlertType.EMAIL

    def test_alert_type_enum_values(self):
        assert AlertType.EMAIL.value == "EMAIL"
        assert AlertType.SMS.value == "SMS"
        assert AlertType.WEBHOOK.value == "WEBHOOK"
        assert AlertType.DASHBOARD.value == "DASHBOARD"

    def test_empty_message_raises(self):
        with pytest.raises(ValidationError):
            AlertCreate(
                violation_id=1,
                alert_type=AlertType.EMAIL,
                recipient="x@y.com",
                message="",
            )

    def test_metadata_optional_defaults_none(self):
        a = AlertCreate(
            violation_id=1,
            alert_type=AlertType.DASHBOARD,
            recipient="dashboard",
            message="Alert",
        )
        assert a.metadata is None

    def test_webhook_alert_type(self):
        a = AlertCreate(
            violation_id=5,
            alert_type=AlertType.WEBHOOK,
            recipient="https://example.com/webhook",
            message="Webhook alert",
        )
        assert a.alert_type == AlertType.WEBHOOK
