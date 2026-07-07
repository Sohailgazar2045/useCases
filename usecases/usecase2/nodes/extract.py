"""extract.py — Module 3: extraction node (OpenAI).

Reads a payment / remittance and returns structured fields with a strict schema
(function calling). Crucially, it captures the LONG TAIL: per-invoice amounts and
**deductions with reason codes** — the "why" behind a short pay (short-ship,
damage, pricing, credit, partial/installment). Downstream matching turns those
reasons into disputes vs. installments vs. credits.

Target shape (populates ``state['extracted']``):
    {
      "customer": "CUST001",
      "payment_channel": "lockbox" | "wire" | "card" | "unknown",
      "currency": "USD",
      "date": "2026-07-01",
      "total_amount": 4800.00,
      "invoices": [
        {
          "invoice_no": "INV-1002",
          "amount_applied": 3000.00,
          "deductions": [
            {"amount": 200.00, "reason_code": "DAMAGE", "note": "2 units arrived broken"}
          ]
        }
      ]
    }
plus ``state['extract_confidence']`` (0-1).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from ..config import LLM_MODEL, REASON_CODES, get_llm_client
from ..state import CashAppState

# Strict schema handed to the model as a function/tool so it must return valid JSON.
EXTRACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "record_remittance",
        "description": "Record the structured fields extracted from a payment/remittance document.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer": {"type": "string", "description": "Customer id or name paying."},
                "payment_channel": {
                    "type": "string",
                    "enum": ["lockbox", "wire", "card", "unknown"],
                    "description": "How the payment arrived: lockbox (check), wire, or card.",
                },
                "currency": {"type": "string", "description": "ISO currency code, e.g. USD."},
                "date": {"type": "string", "description": "Payment date if present (YYYY-MM-DD)."},
                "total_amount": {"type": "number", "description": "Total money received in this payment."},
                "invoices": {
                    "type": "array",
                    "description": "One entry per invoice this payment applies to. Empty if no invoice is referenced.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "invoice_no": {"type": "string", "description": "Invoice number exactly as written."},
                            "amount_applied": {
                                "type": "number",
                                "description": "Money applied to THIS invoice (before/after deductions is fine; see deductions).",
                            },
                            "deductions": {
                                "type": "array",
                                "description": "Amounts withheld from this invoice and WHY. Empty if paid in full.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "amount": {"type": "number", "description": "Amount withheld."},
                                        "reason_code": {
                                            "type": "string",
                                            "enum": list(REASON_CODES.keys()),
                                            "description": "Why this amount was withheld.",
                                        },
                                        "note": {"type": "string", "description": "Verbatim reason text from the document, if any."},
                                    },
                                    "required": ["amount", "reason_code"],
                                },
                            },
                        },
                        "required": ["invoice_no", "amount_applied"],
                    },
                },
                "confidence": {
                    "type": "number",
                    "description": "0-1 self-assessed confidence that ALL fields (including deductions/reasons) are correct and complete.",
                },
            },
            "required": ["customer", "total_amount", "invoices", "confidence"],
        },
    },
}

_REASON_GUIDE = "\n".join(f"    - {code}: {desc}" for code, desc in REASON_CODES.items())

SYSTEM_PROMPT = (
    "You are a cash-application assistant for accounts receivable. You read a single "
    "incoming payment / remittance advice (from lockbox checks, wires, or card) and "
    "extract the fields needed to post it against open invoices.\n\n"
    "Extract, per invoice referenced, how much was applied and — critically — any "
    "DEDUCTIONS (amounts withheld) together with WHY. Classify each deduction's reason:\n"
    f"{_REASON_GUIDE}\n\n"
    "Rules:\n"
    "- Extract ONLY what the document supports. Never invent a customer, invoice number, "
    "amount, or reason.\n"
    "- If no invoice number is referenced at all, return an empty invoices list.\n"
    "- If an invoice is paid in full, its deductions list is empty.\n"
    "- Distinguish an intentional PARTIAL/installment ('paying the rest later') from a "
    "genuine dispute (short-ship, damage, pricing) — use the reason_code accordingly. Use "
    "UNKNOWN only when the amount is short but no reason is given.\n"
    "- Put the customer's own words for a deduction in 'note'.\n"
    "- confidence (0-1): lower it when the document is ambiguous, unreadable, missing an "
    "invoice number, or when deductions don't add up to the shortfall.\n"
    "- Always respond by calling the record_remittance function."
)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _user_content(state: CashAppState) -> list | str:
    """Build the user message: plain text when we have text, otherwise multimodal
    image blocks (scanned PDF / photo of a check) that GPT-4o reads directly."""
    raw_text = state.get("raw_text") or ""
    if raw_text.strip():
        return f"Extract the remittance fields from this payment document:\n\n{raw_text}"

    # Images prepared by the ingest node (scanned PDF pages / uploaded image).
    images = state.get("images") or []
    if images:
        blocks: list = [{"type": "text", "text": "Extract the remittance fields from this payment document."}]
        for uri in images:
            blocks.append({"type": "image_url", "image_url": {"url": uri}})
        return blocks

    # Fallback: read the file directly (e.g. running the node standalone).
    path = Path(state["document_path"])
    if path.suffix.lower() in _IMAGE_EXTS:
        b64 = base64.b64encode(path.read_bytes()).decode()
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        return [
            {"type": "text", "text": "Extract the remittance fields from this payment document."},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]
    return (
        "Extract the remittance fields from this payment document:\n\n"
        + path.read_text(encoding="utf-8", errors="ignore")
    )


def extract(state: CashAppState) -> CashAppState:
    """Graph node: call the LLM to structure the remittance document."""
    client = get_llm_client()
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=0,  # deterministic extraction — same doc → same fields
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_content(state)},
        ],
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "function", "function": {"name": "record_remittance"}},
    )

    tool_calls = resp.choices[0].message.tool_calls
    if not tool_calls:
        return {**state, "extracted": {}, "extract_confidence": 0.0}

    data = json.loads(tool_calls[0].function.arguments)
    confidence = float(data.pop("confidence", 0.0))
    return {**state, "extracted": data, "extract_confidence": confidence}


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else None
    raw = Path(path).read_text(encoding="utf-8", errors="ignore") if path else ""
    result = extract({"document_path": path, "raw_text": raw})
    print(json.dumps({"extracted": result["extracted"],
                      "extract_confidence": result["extract_confidence"]}, indent=2))
