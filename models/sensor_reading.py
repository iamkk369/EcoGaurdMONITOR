"""Pydantic v2 models for sensor readings."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SensorReadingCreate(BaseModel):
    """Payload for creating a new sensor reading."""

    model_config = ConfigDict(str_strip_whitespace=True)

    station_id: str = Field(..., description="Unique station identifier", examples=["VAPI-001"])
    pollutant_type: str = Field(
        ...,
        description="Pollutant code, e.g. SO2, PM25, BOD",
        examples=["PM25"],
    )
    value: float = Field(..., ge=0, description="Measured value")
    unit: str = Field(..., description="Unit of measurement, e.g. µg/m³, mg/L", examples=["µg/m³"])
    timestamp: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Reading timestamp (UTC); defaults to now",
    )
    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lon: Optional[float] = Field(None, ge=-180, le=180)
    sensor_metadata: Optional[dict] = Field(default=None, description="Extra sensor metadata")

    @field_validator("pollutant_type")
    @classmethod
    def normalise_pollutant(cls, v: str) -> str:
        return v.upper().replace(".", "").replace("-", "")

    @field_validator("value")
    @classmethod
    def validate_value_range(cls, v: float) -> float:
        if v > 1_000_000:
            raise ValueError("Sensor value exceeds plausible maximum (1,000,000)")
        return round(v, 4)


class SensorReadingResponse(SensorReadingCreate):
    """Response model including DB-assigned ID."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    created_at: datetime


class BulkSensorReading(BaseModel):
    """Batch ingestion of multiple sensor readings."""

    readings: List[SensorReadingCreate] = Field(
        ..., min_length=1, max_length=500, description="List of sensor readings"
    )
    source: Optional[str] = Field(default="api", description="Ingestion source identifier")
