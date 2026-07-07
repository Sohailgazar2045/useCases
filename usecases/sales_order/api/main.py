"""
api/main.py — FastAPI orchestration layer for Use Case 1 (Sales Order Entry).

Lives inside the use case so the whole feature (pipeline + API + UI) is
self-contained. Run from the repo root:

    uvicorn usecases.sales_order.api.main:app --reload --port 8000

Endpoints
---------
GET  /health           liveness probe
POST /process          multipart (source_kind + file|text) -> extract+match+confidence
POST /orders           JSON {order, match, include_line_indexes?} -> create (idempotent)
GET  /orders           list previously created orders

The Streamlit UI is a thin client over these endpoints. In production the same
routes front Dynamics 365 instead of the mock JSON store.
"""

from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from ..confidence import score_order
from ..extractor import (
    extract_order,
    extract_order_from_image,
    extract_order_from_text,
)
from ..matcher import match_order
from ..order_creator import create_order, load_orders
from .schemas import CreateOrderRequest, ProcessResponse

app = FastAPI(
    title="Sales Order Entry API",
    version="1.0.0",
    description="AI-assisted PO intake → extract → match → confidence → mock D365.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/process", response_model=ProcessResponse)
async def process(
    source_kind: str = Form(..., description="pdf | image | text"),
    text: str | None = Form(None),
    file: UploadFile | None = File(None),
) -> ProcessResponse:
    """Run extraction, matching, and confidence scoring in one call."""
    try:
        if source_kind == "text":
            if not text or not text.strip():
                raise HTTPException(400, "source_kind=text requires a non-empty 'text' field.")
            order = extract_order_from_text(text)
        elif source_kind == "pdf":
            if file is None:
                raise HTTPException(400, "source_kind=pdf requires a 'file' upload.")
            order = extract_order(await file.read())
        elif source_kind == "image":
            if file is None:
                raise HTTPException(400, "source_kind=image requires a 'file' upload.")
            order = extract_order_from_image(await file.read())
        else:
            raise HTTPException(400, f"Unknown source_kind '{source_kind}'.")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface extraction failures as 502
        raise HTTPException(502, f"Extraction failed: {exc}") from exc

    match = match_order(order)
    confidence = score_order(match)
    return ProcessResponse(order=order, match=match, confidence=confidence)


@app.post("/orders")
def create(req: CreateOrderRequest) -> dict:
    """Create (persist) an approved order. Idempotent on PO number."""
    try:
        return create_order(req.match, req.order, req.include_line_indexes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Order creation failed: {exc}") from exc


@app.get("/orders")
def list_orders() -> list[dict]:
    """Return all previously created orders (most recent last)."""
    return load_orders()
