"""
api/main.py — FastAPI orchestration layer for Use Case 3 (AP Invoice Matching).

Self-contained inside the use case (same pattern as Use Case 1's
``usecases/sales_order/api/main.py``), so it does NOT touch or import the UC1
app — nothing existing is affected. Run from the repo root on its own port:

    uvicorn usecases.usecase3.api.main:app --reload --port 8001

Endpoints
---------
GET  /health                liveness probe
POST /uc3/flag-invoice      queue an invoice with variances for human review
GET  /uc3/flagged-invoices  list the review queue
POST /uc3/post-invoice      approve + post an invoice, mint a payment reference
GET  /uc3/check-alerts      goods received but not yet invoiced (accrual risk)

The mock AP ledgers are plain JSON files under ``usecases/usecase3/data/``; in
production these routes would front the ERP's AP module instead.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from ..alert import check_received_not_invoiced
from ..config import (
    FLAGGED_INVOICES_PATH,
    POSTED_INVOICES_PATH,
    RECEIVED_NOT_INVOICED_THRESHOLD_DAYS,
    UC3_CORS_ORIGINS,
)
from ..store import append_json, read_json, write_json
from .schemas import FlagInvoiceRequest, PostInvoiceRequest

app = FastAPI(
    title="AP Invoice Matching API",
    version="1.0.0",
    description="3-way match (invoice ↔ PO ↔ receipt) → flag / post / alerts over a mock AP ledger.",
)

# CORS: the Streamlit UI (and any browser client) calls these routes cross-origin.
# Origins are env-driven (UC3_CORS_ORIGINS) so a public deploy can be locked to
# the Streamlit domain. Credentials can't be combined with the "*" wildcard, so
# only enable them when explicit origins are configured.
_allow_credentials = UC3_CORS_ORIGINS != ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=UC3_CORS_ORIGINS,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now() -> str:
    """Timestamp in the same format UC1's order_creator uses."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/uc3/flag-invoice")
def flag_invoice(req: FlagInvoiceRequest) -> dict:
    """Append an invoice to the review queue with a 'Pending Review' status."""
    try:
        record = {
            "invoice_number": req.invoice_number,
            "vendor_name": req.vendor_name,
            "po_number": req.po_number,
            "total_amount": req.total_amount,
            "match_result": req.match_result,
            "status": "Pending Review",  # forced regardless of what the client sent
            "flagged_at": _now(),
        }
        append_json(FLAGGED_INVOICES_PATH, record)  # creates the file if absent
        return {"status": "flagged", "invoice": record}
    except Exception as exc:  # noqa: BLE001 — surface persistence failures as 500
        raise HTTPException(500, f"Failed to flag invoice: {exc}") from exc


@app.get("/uc3/flagged-invoices")
def flagged_invoices() -> list[dict]:
    """Return every flagged invoice (empty list if the file doesn't exist yet)."""
    try:
        return read_json(FLAGGED_INVOICES_PATH, default=[])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to read flagged invoices: {exc}") from exc


@app.post("/uc3/post-invoice")
def post_invoice(req: PostInvoiceRequest) -> dict:
    """
    Post an approved invoice: mint a sequential payment reference, append it to
    the posted ledger, and mark any matching flagged invoice as Posted.
    """
    try:
        posted = read_json(POSTED_INVOICES_PATH, default=[])
        if not isinstance(posted, list):
            posted = []

        # Sequential payment reference: PAY-2026-00001, 00002, ... based on count.
        payment_ref = f"PAY-2026-{len(posted) + 1:05d}"
        posted_at = _now()

        record = {
            "invoice_number": req.invoice_number,
            "vendor": req.vendor,
            "po_number": req.po_number,
            "invoice_amount": req.invoice_amount,
            "match_result": req.match_result,
            "status": req.status or "Posted",
            "approved_by": req.approved_by,
            "payment_ref": payment_ref,
            "posted_at": posted_at,
        }
        posted.append(record)
        write_json(POSTED_INVOICES_PATH, posted)

        # Reflect the outcome back onto the review queue, if this invoice was flagged.
        flagged = read_json(FLAGGED_INVOICES_PATH, default=[])
        updated = False
        if isinstance(flagged, list):
            for rec in flagged:
                if rec.get("invoice_number") == req.invoice_number:
                    rec["status"] = "Posted"
                    rec["payment_ref"] = payment_ref
                    rec["posted_at"] = posted_at
                    updated = True
            if updated:
                write_json(FLAGGED_INVOICES_PATH, flagged)

        return {
            "status": "posted",
            "payment_ref": payment_ref,
            "flagged_updated": updated,
            "invoice": record,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to post invoice: {exc}") from exc


@app.get("/uc3/check-alerts")
def check_alerts() -> list[dict]:
    """Goods received but not invoiced within the threshold window."""
    try:
        return check_received_not_invoiced(
            threshold_days=RECEIVED_NOT_INVOICED_THRESHOLD_DAYS
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to compute alerts: {exc}") from exc
