"""
extractor.py — Extract structured purchase-order data from a PDF using OpenAI.

Flow:
    PDF bytes/path
      -> pdfplumber extracts text
      -> if text is empty (scanned/image PDF) -> render page to PNG and use
         OpenAI vision
      -> send to OpenAI with a strict JSON prompt
      -> parse and return structured order data (dict)
"""

from __future__ import annotations

import base64
import io
import json
import os
from typing import Any

import pdfplumber
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

EXTRACTION_PROMPT = """You are an AI that extracts purchase order data from documents.

Extract the following fields and return ONLY valid JSON:
{
  "customer_name": "",
  "po_number": "",
  "order_date": "",
  "delivery_date": "",
  "shipping_address": "",
  "line_items": [
    {
      "part_number": "",
      "description": "",
      "quantity": 0,
      "unit_price": 0.0
    }
  ]
}

Rules:
- If a field is not found, return null for that field.
- If a part number is not found for a line item, set "part_number" to null and
  still extract the "description".
- quantity must be an integer, unit_price must be a number (no currency symbol).
- Return ONLY the JSON. No explanation, no markdown fences.
"""


def _client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return OpenAI(api_key=api_key)


def _read_pdf_bytes(pdf_file: Any) -> bytes:
    """Accept a path, bytes, or a file-like object (e.g. Streamlit upload)."""
    if isinstance(pdf_file, (bytes, bytearray)):
        return bytes(pdf_file)
    if isinstance(pdf_file, str):
        with open(pdf_file, "rb") as fh:
            return fh.read()
    # file-like (has .read); Streamlit UploadedFile also supports getvalue()
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


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` fences if the model added them despite instructions."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _call_openai_text(client: OpenAI, document_text: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": f"Document text:\n\n{document_text}"},
        ],
    )
    return resp.choices[0].message.content or "{}"


def _call_openai_vision(client: OpenAI, png_bytes: bytes) -> str:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the purchase order from this image."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            },
        ],
    )
    return resp.choices[0].message.content or "{}"


def _normalize(order: dict[str, Any]) -> dict[str, Any]:
    """Guarantee the expected shape so downstream code never KeyErrors."""
    order.setdefault("customer_name", None)
    order.setdefault("po_number", None)
    order.setdefault("order_date", None)
    order.setdefault("delivery_date", None)
    order.setdefault("shipping_address", None)
    items = order.get("line_items") or []
    clean_items = []
    for it in items:
        clean_items.append(
            {
                "part_number": (it.get("part_number") or None),
                "description": (it.get("description") or None),
                "quantity": _to_int(it.get("quantity")),
                "unit_price": _to_float(it.get("unit_price")),
            }
        )
    order["line_items"] = clean_items
    return order


def _to_int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, str):
        v = v.replace("$", "").replace(",", "").strip()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_order(pdf_file: Any) -> dict[str, Any]:
    """
    Main entry point. Returns a normalized order dict:
        {
          customer_name, po_number, order_date, delivery_date,
          shipping_address, line_items: [...], _source: "text"|"vision"
        }
    """
    pdf_bytes = _read_pdf_bytes(pdf_file)
    client = _client()

    text = extract_text(pdf_bytes)
    if text:
        raw = _call_openai_text(client, text)
        source = "pdf_text"
    else:
        png = _render_first_page_png(pdf_bytes)
        raw = _call_openai_vision(client, png)
        source = "pdf_scanned"

    return _parse_and_normalize(raw, source)


def _parse_and_normalize(raw: str, source: str) -> dict[str, Any]:
    """Shared tail: parse the model's JSON, normalize shape, tag the source."""
    try:
        order = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI did not return valid JSON: {exc}\n\nRaw:\n{raw}")
    order = _normalize(order)
    order["_source"] = source
    return order


def extract_order_from_text(document_text: str) -> dict[str, Any]:
    """
    Extract an order from raw text (e.g. a typed / forwarded email body),
    skipping the PDF step. Same normalized shape as extract_order().
    """
    if not document_text or not document_text.strip():
        raise ValueError("No text provided to extract from.")
    client = _client()
    raw = _call_openai_text(client, document_text.strip())
    return _parse_and_normalize(raw, source="email_text")


def _to_png(image_bytes: bytes) -> bytes:
    """Normalize any uploaded image (JPG/PNG/etc.) to PNG bytes for the API."""
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def extract_order_from_image(image_bytes: bytes) -> dict[str, Any]:
    """
    Extract an order from an image file (scanned photo or handwritten form)
    using OpenAI vision. Same normalized shape as extract_order().
    """
    client = _client()
    raw = _call_openai_vision(client, _to_png(image_bytes))
    return _parse_and_normalize(raw, source="image")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python extractor.py <path-to-pdf>")
        raise SystemExit(1)
    print(json.dumps(extract_order(sys.argv[1]), indent=2))
