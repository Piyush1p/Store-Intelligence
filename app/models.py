from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class StoreEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    store_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    visitor_id: str = Field(min_length=1)
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = Field(ge=0)
    is_staff: bool
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @field_validator("zone_id")
    @classmethod
    def normalize_zone_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().upper().replace(" ", "_")
        return clean or None


class PosTransaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1)
    store_id: str = Field(min_length=1)
    timestamp: datetime
    basket_value_inr: float = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class IngestError(BaseModel):
    index: int
    event_id: str | None = None
    error: str


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    errors: list[IngestError]

