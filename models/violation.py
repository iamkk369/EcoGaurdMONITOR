"""Pydantic v2 models for pollution violations."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ViolationSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ViolationStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    DISPUTED = "DISPUTED"


class ViolationCreate(BaseModel):
    """Payload for creating a violation record."""

    model_config = ConfigDict(str_strip_whitespace=True)

    station_id: str = Field(..., description="Station where the violation occurred")
    pollutant_type: str = Field(..., description="Pollutant code")
    measured_value: float = Field(..., ge=0, description="Observed pollutant concentration")
    limit_value: float = Field(..., gt=0, description="Regulatory threshold")
    unit: str = Field(..., description="Unit of measurement")
    severity: ViolationSeverity = Field(..., description="Computed severity level")
    timestamp: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    report_text: Optional[str] = Field(None, description="IBM Granite-generated violation report")
    notes: Optional[str] = Field(None, description="Additional notes")

    @property
    def exceedance_ratio(self) -> float:
        """How many times the limit has been exceeded."""
        return self.measured_value / self.limit_value if self.limit_value else 0.0


class ViolationResponse(ViolationCreate):
    """Response model including DB-assigned fields."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    status: ViolationStatus = ViolationStatus.OPEN
    created_at: datetime
    updated_at: datetime
