"""
api/schemas.py — Pydantic request models for the Use Case 3 (AP) backend.

``match_result`` is whatever the matcher produced (usually the full match dict,
sometimes just a label string), so it's modeled as an open value rather than
re-declaring the matcher's shape here.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class FlagInvoiceRequest(BaseModel):
    """Route an invoice with variances into the human-review queue."""

    invoice_number: str
    vendor: str
    po_number: str
    invoice_amount: float
    match_result: str
    status: Optional[str] = "Pending Review"
    approved_by: Optional[str] = "System"


class MatchInvoiceLineItem(BaseModel):
    """One invoice line, in the shape ``matcher.match_invoice`` expects."""

    part_number: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None


class MatchInvoiceRequest(BaseModel):
    """An extracted invoice to run through the 3-way match."""

    vendor_name: str
    invoice_number: str
    po_number: str
    invoice_date: Optional[str] = None
    line_items: list[MatchInvoiceLineItem] = Field(default_factory=list)
    total_amount: Optional[float] = None


class ExtractTextBase64Request(BaseModel):
    """Extract text from a base64-encoded file (JSON-in, avoids multipart)."""

    content: str = Field(..., description="Base64-encoded file content.")
    filename: str = Field(..., description="Original filename — its extension picks the handler.")


class SaveAlertRequest(BaseModel):
    """Persist a 'received-not-invoiced' alert to the audit trail."""

    receipt_id: str
    vendor: str
    po_number: str
    received_date: str
    days_overdue: int
    status: Optional[str] = Field(
        None, description="Ignored — the server forces 'Alert - Pending Action'."
    )


class PostInvoiceRequest(BaseModel):
    """Approve + post an invoice to the (mock) AP ledger for payment."""

    invoice_number: str
    vendor: str
    po_number: str
    invoice_amount: float
    match_result: Optional[Any] = Field(
        None, description="The matcher output (dict) or a summary label."
    )
    status: Optional[str] = Field(None, description="Defaults to 'Posted' if omitted.")
    approved_by: str
