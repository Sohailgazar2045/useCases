"""
edits.py — Diff a human-corrected order against the AI's original extraction.

Pure and dependency-free so it's usable from both the UI (the "what changed"
banner) and order_creator.py (the persisted audit trail).
"""

from __future__ import annotations

from typing import Any

_HEADER_FIELDS = ("customer_name", "po_number", "delivery_date", "shipping_address")
_LINE_FIELDS = ("part_number", "description", "quantity", "unit_price")


def diff_order(original: dict[str, Any] | None, corrected: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return a list of {field, was, now} for every value that changed.

    Compares header fields directly and line items positionally (by index),
    covering added lines (was=None) and removed lines (now=None).
    """
    if not original or not corrected:
        return []

    changes: list[dict[str, Any]] = []

    for field in _HEADER_FIELDS:
        was = original.get(field)
        now = corrected.get(field)
        if was != now:
            changes.append({"field": field, "was": was, "now": now})

    orig_lines = original.get("line_items") or []
    corr_lines = corrected.get("line_items") or []
    max_len = max(len(orig_lines), len(corr_lines))

    for idx in range(max_len):
        was_line = orig_lines[idx] if idx < len(orig_lines) else None
        now_line = corr_lines[idx] if idx < len(corr_lines) else None

        if was_line is None:
            changes.append({"field": f"line_items[{idx}]", "was": None, "now": "(added)"})
            continue
        if now_line is None:
            changes.append({"field": f"line_items[{idx}]", "was": "(present)", "now": "(removed)"})
            continue

        for field in _LINE_FIELDS:
            was = was_line.get(field)
            now = now_line.get(field)
            if was != now:
                changes.append({"field": f"line_items[{idx}].{field}", "was": was, "now": now})

    return changes
