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
POST /uc3/match-invoice     3-way match one invoice (invoice ↔ PO ↔ receipt)
GET  /uc3/flagged-invoices  list the review queue
POST /uc3/post-invoice      approve + post an invoice, mint a payment reference
GET  /uc3/check-alerts      goods received but not yet invoiced (accrual risk)
POST /uc3/save-alert        persist an alert to the audit trail
GET  /uc3/alerts            list every saved alert
POST /uc3/extract-text      pull raw text / base64 from an uploaded PDF/image/TXT
POST /uc3/extract-text-base64  same, but JSON-in (base64) — Power Automate friendly

The mock AP ledgers are plain JSON files under ``usecases/usecase3/data/``; in
production these routes would front the ERP's AP module instead.
"""

from __future__ import annotations

import base64
import traceback
from datetime import datetime

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from ..alert import check_received_not_invoiced
from ..config import (
    ALERTS_PATH,
    FLAGGED_INVOICES_PATH,
    POSTED_INVOICES_PATH,
    PURCHASE_ORDERS_PATH,
    RECEIPTS_PATH,
    RECEIVED_NOT_INVOICED_THRESHOLD_DAYS,
    UC3_CORS_ORIGINS,
)
from ..matcher import match_invoice
from ..store import append_json, read_json, write_json
from .schemas import (
    ExtractTextBase64Request,
    FlagInvoiceRequest,
    MatchInvoiceRequest,
    PostInvoiceRequest,
    SaveAlertRequest,
)

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


@app.post("/uc3/match-invoice")
def match_invoice_endpoint(req: MatchInvoiceRequest) -> dict:
    """
    Run the 3-way match for one extracted invoice against the PO master and the
    goods-receipt ledger, and return the full match verdict.

    Reads ``purchase_orders.json`` and ``receipts.json`` from the data folder and
    delegates the comparison to ``matcher.match_invoice`` (pure Python, no LLM).
    """
    try:
        purchase_orders = read_json(PURCHASE_ORDERS_PATH, default=[])
        receipts = read_json(RECEIPTS_PATH, default=[])

        # Shape the invoice exactly as match_invoice expects: line_items carry
        # part_number / description / quantity / unit_price.
        invoice = {
            "invoice_number": req.invoice_number,
            "vendor": req.vendor_name,
            "po_number": req.po_number,
            "invoice_date": req.invoice_date,
            "total_amount": req.total_amount,
            "line_items": [li.model_dump() for li in req.line_items],
        }

        result = match_invoice(invoice, purchase_orders, receipts)
        return {
            "match_result": result["match_result"],
            "auto_approve": result["auto_approve"],
            "flags": result["flags"],
            "line_results": result["line_results"],
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to match invoice: {exc}") from exc


@app.post("/uc3/flag-invoice")
def flag_invoice(req: FlagInvoiceRequest) -> dict:
    """Append an invoice to the review queue with a 'Pending Review' status."""
    try:
        record = {
            "invoice_number": req.invoice_number,
            "vendor": req.vendor,
            "po_number": req.po_number,
            "invoice_amount": req.invoice_amount,
            "match_result": req.match_result,
            "status": req.status or "Pending Review",
            "approved_by": req.approved_by or "System",
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


@app.post("/uc3/save-alert")
def save_alert(req: SaveAlertRequest) -> dict:
    """Persist a received-not-invoiced alert to its own audit trail."""
    try:
        record = {
            "receipt_id": req.receipt_id,
            "vendor": req.vendor,
            "po_number": req.po_number,
            "received_date": req.received_date,
            "days_overdue": req.days_overdue,
            "status": "Alert - Pending Action",  # forced regardless of client input
            "alerted_at": _now(),
        }
        append_json(ALERTS_PATH, record)  # creates the file if absent
        return {"status": "saved", "alert": record}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to save alert: {exc}") from exc


@app.get("/uc3/alerts")
def alerts() -> list[dict]:
    """Return every saved alert (empty list if the file doesn't exist yet)."""
    try:
        return read_json(ALERTS_PATH, default=[])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Failed to read alerts: {exc}") from exc


@app.post("/uc3/extract-text")
async def extract_text_endpoint(file: UploadFile = File(...)) -> dict:
    """
    Extract raw content from an uploaded document for the caller to hand to an
    LLM/vision model. This endpoint does NO OpenAI call — it only extracts.

    - PDF        → pdfplumber text from every page (reuses UC1's ``extract_text``)
    - PNG / JPG  → base64 string (the caller feeds it to a vision model)
    - TXT        → decoded file contents

    Same error-handling shape as UC1's ``/process``: known HTTP errors pass
    through; anything else surfaces as a 502 "Extraction failed".
    """
    filename = (file.filename or "").lower()
    try:
        data = await file.read()

        if filename.endswith(".pdf"):
            # Reuse UC1's text extractor. Imported lazily so image/txt uploads —
            # and every other UC3 endpoint — stay free of the heavier UC1 deps
            # (pdfplumber/LangChain), keeping the standalone UC3 image small.
            from usecases.sales_order.extractor import extract_text as _extract_pdf_text

            text = _extract_pdf_text(data)
            source = "pdf"
        elif filename.endswith((".png", ".jpg", ".jpeg")):
            text = base64.b64encode(data).decode("ascii")
            source = "image"
        elif filename.endswith(".txt"):
            text = data.decode("utf-8", errors="ignore")
            source = "txt"
        else:
            raise HTTPException(
                400,
                f"Unsupported file type: {file.filename!r}. Supported: PDF, PNG, JPG, TXT.",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface extraction failures as 502
        raise HTTPException(502, f"Extraction failed: {exc}") from exc

    return {"text": text, "source": source, "success": True}


def _decode_incoming(content: str) -> bytes:
    """
    Decode the ``content`` string Power Automate sends. It doesn't always emit
    clean, correctly-padded base64, so try progressively looser strategies:

      1. Standard base64 decode.
      2. Base64 decode after re-padding to a multiple of 4 with '='.
      3. Treat the string as raw UTF-8 text (it wasn't base64 at all).
    """
    # 1. Standard base64.
    try:
        return base64.b64decode(content, validate=True)
    except Exception:
        pass
    # 2. Re-pad, then base64 (lenient — ignores stray whitespace/newlines).
    try:
        padded = content + "=" * (-len(content) % 4)
        return base64.b64decode(padded)
    except Exception:
        pass
    # 3. Not base64 — the raw string is already the content.
    return content.encode("utf-8")


@app.post("/uc3/extract-text-base64")
def extract_text_base64(req: ExtractTextBase64Request) -> dict:
    """
    JSON-in twin of ``/uc3/extract-text`` — accepts base64 content instead of a
    multipart upload, so clients like Power Automate that struggle with
    multipart/form-data can just POST JSON. No OpenAI call.

    Never raises to the client: on any failure it returns HTTP 200 with
    ``{"text": "", "source": "error", "success": false, "error": "..."}`` so
    Power Automate can read the message instead of choking on a 500.
    """
    try:
        filename = (req.filename or "").lower()
        print(f"[extract-text-base64] filename received: {req.filename!r}")
        print(f"[extract-text-base64] content length received: {len(req.content or '')}")

        data = _decode_incoming(req.content or "")

        if filename.endswith(".pdf"):
            # Reuse UC1's text extractor (lazy import — see /uc3/extract-text).
            from usecases.sales_order.extractor import extract_text as _extract_pdf_text

            text = _extract_pdf_text(data)
            source = "pdf"
        elif filename.endswith((".png", ".jpg", ".jpeg")):
            # Re-encode the decoded bytes to clean base64 for the vision model.
            text = base64.b64encode(data).decode("ascii")
            source = "image"
        elif filename.endswith(".txt"):
            text = data.decode("utf-8", errors="ignore")
            source = "txt"
        else:
            # Unknown extension — best-effort UTF-8 decode.
            text = data.decode("utf-8", errors="ignore")
            source = "txt"

        print(f"[extract-text-base64] first 100 chars of decoded text: {(text or '')[:100]!r}")
        return {"text": text, "source": source, "success": True}

    except Exception as exc:  # noqa: BLE001 — never 500 to Power Automate
        traceback.print_exc()
        return {"text": "", "source": "error", "success": False, "error": str(exc)}
