"""Pydantic v2 models for regulatory alerts."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class AlertType(str, Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    WEBHOOK = "WEBHOOK"
    DASHBOARD = "DASHBOARD"


class AlertStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SUPPRESSED = "SUPPRESSED"


class AlertCreate(BaseModel):
    """Payload for creating an alert."""

    model_config = ConfigDict(str_strip_whitespace=True)

    violation_id: int = Field(..., description="Reference to the triggering violation")
    alert_type: AlertType = Field(..., description="Delivery channel")
    recipient: str = Field(..., description="Email address, phone number, or webhook URL")
    message: str = Field(..., min_length=1, description="Alert message body")
    metadata: Optional[dict] = Field(default=None, description="Channel-specific metadata")


class AlertResponse(AlertCreate):
    """Response model including DB-assigned fields."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    id: int
    status: AlertStatus = AlertStatus.PENDING
    sent_at: Optional[datetime] = None
    created_at: datetime
