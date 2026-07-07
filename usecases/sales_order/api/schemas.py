"""
api/schemas.py — Pydantic request/response models for the Use Case 1 backend.

The `order` and `match` payloads are already-normalized dicts produced by the
sales-order pipeline, so they're modeled as open dicts rather than re-declaring
every field here (the pipeline owns that shape).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ProcessResponse(BaseModel):
    """Result of /process — extraction + matching + confidence in one shot."""

    order: dict[str, Any]
    match: dict[str, Any]
    confidence: dict[str, Any]


class CreateOrderRequest(BaseModel):
    """Approve an order (optionally only some lines) → create in mock D365."""

    order: dict[str, Any] = Field(..., description="The extracted order dict")
    match: dict[str, Any] = Field(..., description="The match result dict")
    include_line_indexes: Optional[list[int]] = Field(
        None,
        description="0-based line indexes to create; omit to create all lines "
        "(used for partial-order approval).",
    )


class ErrorResponse(BaseModel):
    detail: str
