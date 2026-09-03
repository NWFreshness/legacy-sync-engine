"""Pydantic v2 models — verbatim from docs/api_contract.md v1.0 (signed).

This file is the machine-checkable half of the contract. If it drifts from
docs/api_contract.md, that is a bug: the contract is binding.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

SyncDirection = Literal["modern_to_legacy", "legacy_to_modern"]
ResolutionStrategy = Literal["last_write_wins", "source_of_truth_wins", "manual_review"]
SyncStatus = Literal["pending", "applied", "conflicted", "failed", "dead_lettered"]


class MappingField(BaseModel):
    modern_field: str
    legacy_field: str
    type: str  # "string", "integer", "datetime", "numeric"
    direction: Literal["both", "modern_to_legacy", "legacy_to_modern"]
    required: bool = False
    transform: dict[str, str] | None = None
    default: Any = None


class TableMapping(BaseModel):
    version: str
    table: str
    source_of_truth: Literal["modern", "legacy", "both"]
    conflict_strategy: ResolutionStrategy
    clock_skew_tolerance_seconds: int = 5
    fields: list[MappingField]


class SyncEvent(BaseModel):
    event_id: UUID
    direction: SyncDirection
    table_name: str
    record_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    mapping_version: str
    strategy: ResolutionStrategy
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ProposedResolution(BaseModel):
    """One selectable resolution option surfaced to the admin UI."""

    model_config = ConfigDict(populate_by_name=True)

    name: str  # "modern" | "legacy"
    state: dict[str, Any]


class Conflict(BaseModel):
    conflict_id: UUID
    table_name: str
    record_id: str
    modern_state: dict[str, Any]
    legacy_state: dict[str, Any]
    detected_at: datetime
    strategy: ResolutionStrategy = "manual_review"
    status: Literal["pending", "resolved"] = "pending"
    proposed_resolutions: list[dict[str, Any]] = Field(default_factory=list)


class ResolveConflictRequest(BaseModel):
    conflict_id: UUID
    chosen_state: dict[str, Any]  # the winning payload
    resolution_notes: str | None = None
    resolved_by: str = "admin"


class WebhookPayload(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)  # idempotency key
    table: str
    record: dict[str, Any]
    operation: Literal["insert", "update", "delete"]
    source: Literal["modern", "legacy"]
    timestamp: datetime


class SyncTriggerRequest(BaseModel):
    table: Literal["customers", "orders", "inventory"]
    direction: SyncDirection | Literal["both"] = "both"


class SyncTriggerResponse(BaseModel):
    job_id: UUID
    status: str
    message: str


class AuditEntry(BaseModel):
    event_id: UUID
    created_at: datetime
    direction: SyncDirection
    table_name: str
    record_id: str
    resolution_strategy: ResolutionStrategy
    status: SyncStatus
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class HealthResponse(BaseModel):
    status: str
    modern_db: Literal["up", "down"]
    legacy_db: Literal["up", "down"]
    last_sync: dict[str, str | None]
    mapping_versions: list[str]


class MappingSummary(BaseModel):
    version: str
    table: str
    source_of_truth: str
    conflict_strategy: ResolutionStrategy
    clock_skew_tolerance_seconds: int
