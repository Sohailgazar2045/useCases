"""ingest.py — Module 2: ingestion node.

Accepts a payment document — **txt, PDF (text or scanned), or image** — detects
its type, and loads it into state so the extractor can read it:

  - text / text-PDF  → ``raw_text`` (the LLM reads the text)
  - image / scanned PDF → ``images`` (data-URIs the LLM reads multimodally)

This is the "a payment arrives" step: in the demo a file is uploaded; in
production the same slot receives lockbox/wire/card feeds + scanned checks.
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..state import CashAppState

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_EXT_TO_TYPE = {
    ".pdf": "remittance",
    ".txt": "remittance",
    ".png": "check",
    ".jpg": "check",
    ".jpeg": "check",
}
_MAX_PDF_PAGES = 3  # cap pages rendered for scanned PDFs (demo-sane)


def detect_document_type(path: str | Path) -> str:
    """Best-effort classification from file extension (refine with the LLM later)."""
    return _EXT_TO_TYPE.get(Path(path).suffix.lower(), "remittance")


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _load_pdf(path: Path) -> tuple[str, list[str]]:
    """Return (raw_text, images). Prefer real text; fall back to rendering pages
    to images for scanned PDFs. Heavy libs are imported lazily."""
    text = ""
    try:
        import pdfplumber  # lazy

        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        text = ""

    if text.strip():
        return text, []

    # No embedded text → scanned. Render pages to PNGs for multimodal reading.
    images: list[str] = []
    try:
        import fitz  # PyMuPDF, lazy

        doc = fitz.open(str(path))
        for page in list(doc)[:_MAX_PDF_PAGES]:
            pix = page.get_pixmap(dpi=150)
            images.append(_data_uri(pix.tobytes("png"), "image/png"))
        doc.close()
    except Exception:
        pass
    return "", images


def ingest(state: CashAppState) -> CashAppState:
    """Graph node: load the incoming document into state (text or images)."""
    path = Path(state["document_path"])
    suffix = path.suffix.lower()
    doc_type = detect_document_type(path)

    raw_text, images = "", []
    if suffix in _IMAGE_EXTS:
        mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
        images = [_data_uri(path.read_bytes(), mime)]
    elif suffix == ".pdf":
        raw_text, images = _load_pdf(path)
    else:  # .txt and anything else readable as text
        raw_text = path.read_text(encoding="utf-8", errors="ignore")

    return {**state, "document_type": doc_type, "raw_text": raw_text, "images": images}
