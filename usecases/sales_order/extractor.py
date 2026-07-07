"""
extractor.py — Extract structured purchase-order data with LangChain + OpenAI.

Flow:
    PDF bytes/path / raw text / image bytes
      -> pdfplumber pulls text from text-based PDFs
      -> scanned PDFs and images are rendered/normalized to PNG
      -> LangChain (ChatOpenAI, structured output) returns a typed PurchaseOrder
      -> the Pydantic result is normalized to the dict shape downstream expects

LangChain provides the prompt template, model abstraction, and structured-output
parsing; OpenAI GPT-4o (text + vision) is the underlying model.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any, List, Optional

import pdfplumber
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from shared.config import OPENAI_MODEL as MODEL
from shared.config import get_openai_api_key


# --------------------------------------------------------------------------- #
# Structured-output schema (LangChain coerces the model's reply into this)
# --------------------------------------------------------------------------- #
class LineItem(BaseModel):
    """A single ordered product line."""

    part_number: Optional[str] = Field(None, description="Catalog/part number, if present")
    description: Optional[str] = Field(None, description="Free-text item description")
    quantity: Optional[int] = Field(None, description="Ordered quantity as an integer")
    unit_price: Optional[float] = Field(None, description="Price per unit, no currency symbol")


class PurchaseOrder(BaseModel):
    """The full extracted purchase order."""

    customer_name: Optional[str] = None
    po_number: Optional[str] = None
    order_date: Optional[str] = None
    delivery_date: Optional[str] = None
    shipping_address: Optional[str] = None
    line_items: List[LineItem] = Field(default_factory=list)


EXTRACTION_SYSTEM = """You are an AI that extracts purchase-order data from documents.

Extract the customer, PO number, order/delivery dates, shipping address, and every
line item (part number, description, quantity, unit price).

Rules:
- If a field is not present in the document, return null for it.
- If a line item has no part number, set part_number to null but still capture the
  description.
- quantity must be an integer; unit_price must be a plain number with no currency symbol.
"""


# --------------------------------------------------------------------------- #
# LangChain model
# --------------------------------------------------------------------------- #
def _structured_llm():
    """A ChatOpenAI bound to the PurchaseOrder schema via structured output."""
    llm = ChatOpenAI(model=MODEL, temperature=0, api_key=get_openai_api_key())
    return llm.with_structured_output(PurchaseOrder)


# --------------------------------------------------------------------------- #
# Input helpers (unchanged behavior)
# --------------------------------------------------------------------------- #
def _read_pdf_bytes(pdf_file: Any) -> bytes:
    """Accept a path, bytes, or a file-like object (e.g. Streamlit upload)."""
    if isinstance(pdf_file, (bytes, bytearray)):
        return bytes(pdf_file)
    if isinstance(pdf_file, str):
        with open(pdf_file, "rb") as fh:
            return fh.read()
    if hasattr(pdf_file, "getvalue"):
        return pdf_file.getvalue()
    data = pdf_file.read()
    return data if isinstance(data, bytes) else bytes(data)


def extract_text(pdf_bytes: bytes) -> str:
    """Pull text out of a text-based PDF with pdfplumber."""
    chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks).strip()


def _render_first_page_png(pdf_bytes: bytes) -> bytes:
    """Render page 1 to a PNG for the vision fallback (uses PyMuPDF)."""
    import fitz  # PyMuPDF — imported lazily so text-only PDFs don't need it

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=200)
    return pix.tobytes("png")


def _to_png(image_bytes: bytes) -> bytes:
    """Normalize any uploaded image (JPG/PNG/etc.) to PNG bytes for the API."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# --------------------------------------------------------------------------- #
# Model invocation via LangChain
# --------------------------------------------------------------------------- #
def _invoke_text(document_text: str) -> PurchaseOrder:
    messages = [
        SystemMessage(content=EXTRACTION_SYSTEM),
        HumanMessage(content=f"Document text:\n\n{document_text}"),
    ]
    return _structured_llm().invoke(messages)


def _invoke_vision(png_bytes: bytes) -> PurchaseOrder:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    messages = [
        SystemMessage(content=EXTRACTION_SYSTEM),
        HumanMessage(
            content=[
                {"type": "text", "text": "Extract the purchase order from this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]
        ),
    ]
    return _structured_llm().invoke(messages)


# --------------------------------------------------------------------------- #
# Normalization — guarantee the dict shape downstream code depends on
# --------------------------------------------------------------------------- #
def _normalize(po: PurchaseOrder, source: str) -> dict[str, Any]:
    order = {
        "customer_name": po.customer_name or None,
        "po_number": po.po_number or None,
        "order_date": po.order_date or None,
        "delivery_date": po.delivery_date or None,
        "shipping_address": po.shipping_address or None,
        "line_items": [
            {
                "part_number": it.part_number or None,
                "description": it.description or None,
                "quantity": it.quantity,
                "unit_price": it.unit_price,
            }
            for it in po.line_items
        ],
        "_source": source,
    }
    return order


# --------------------------------------------------------------------------- #
# Public entry points (stable signatures)
# --------------------------------------------------------------------------- #
def extract_order(pdf_file: Any) -> dict[str, Any]:
    """Extract from a PDF (text layer if present, else vision on page 1)."""
    pdf_bytes = _read_pdf_bytes(pdf_file)
    text = extract_text(pdf_bytes)
    if text:
        return _normalize(_invoke_text(text), "pdf_text")
    png = _render_first_page_png(pdf_bytes)
    return _normalize(_invoke_vision(png), "pdf_scanned")


def extract_order_from_text(document_text: str) -> dict[str, Any]:
    """Extract from raw text (e.g. a typed / forwarded email body)."""
    if not document_text or not document_text.strip():
        raise ValueError("No text provided to extract from.")
    return _normalize(_invoke_text(document_text.strip()), "email_text")


def extract_order_from_image(image_bytes: bytes) -> dict[str, Any]:
    """Extract from an image (scanned photo or handwritten form) via vision."""
    return _normalize(_invoke_vision(_to_png(image_bytes)), "image")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python extractor.py <path-to-pdf>")
        raise SystemExit(1)
    print(json.dumps(extract_order(sys.argv[1]), indent=2))
