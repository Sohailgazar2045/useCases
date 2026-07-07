"""
order_creator.py — Mock Dynamics 365 F&O sales-order creation.

In production this would call the D365 OData / integration API with real
credentials. Here it fabricates a plausible confirmation so the demo can show
the full happy path end-to-end.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

# Local JSON "database" that stands in for D365. Every approved order is
# appended here so the demo has a visible, persistent audit trail.
ORDERS_DB = os.path.join(os.path.dirname(__file__), "orders.json")


def _next_order_id(existing: list[dict[str, Any]]) -> str:
    """Continue the SO number sequence from whatever is already saved."""
    seq = 123 + len(existing)
    return f"SO-2026-{seq + 1:05d}"


def load_orders() -> list[dict[str, Any]]:
    """Return all previously saved orders (empty list if none yet)."""
    if not os.path.exists(ORDERS_DB):
        return []
    try:
        with open(ORDERS_DB, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def _save_order(confirmation: dict[str, Any]) -> None:
    orders = load_orders()
    orders.append(confirmation)
    with open(ORDERS_DB, "w", encoding="utf-8") as fh:
        json.dump(orders, fh, indent=2, ensure_ascii=False)


def find_by_po(po_number: str | None) -> dict[str, Any] | None:
    """Return a previously created order with this PO number, if any."""
    if not po_number:
        return None
    for existing in load_orders():
        if existing.get("po_number") == po_number:
            return existing
    return None


def create_order(
    match_result: dict[str, Any],
    order: dict[str, Any],
    include_line_indexes: list[int] | None = None,
) -> dict[str, Any]:
    """
    Receives the approved match result + original extracted order, generates a
    mock D365 confirmation, PERSISTS it to orders.json, and returns it.

    Idempotency: if an order with the same PO number already exists it is NOT
    duplicated — the existing confirmation is returned with status "duplicate".

    Partial orders: pass ``include_line_indexes`` (0-based) to create an order
    for only the selected lines (e.g. the catalog-matched ones), holding the rest.
    """
    po_number = order.get("po_number")

    # --- Idempotency guard: don't create the same PO twice. ---------------- #
    prior = find_by_po(po_number)
    if prior is not None:
        return {
            **prior,
            "status": "duplicate",
            "message": (
                f"Duplicate PO — order {prior.get('order_id')} already exists for "
                f"PO {po_number}. No new order created."
            ),
        }

    customer = match_result.get("customer", {})
    matched = customer.get("matched") or {}

    existing = load_orders()
    order_id = _next_order_id(existing)

    all_lines = match_result.get("line_items", [])
    if include_line_indexes is not None:
        selected = [(i, all_lines[i]) for i in include_line_indexes if 0 <= i < len(all_lines)]
    else:
        selected = list(enumerate(all_lines))

    # Flatten the approved line items so the saved record is self-contained.
    saved_lines = []
    total = 0.0
    for _, ln in selected:
        item = ln.get("item", {})
        prod = ln.get("product", {}).get("matched") or {}
        saved_lines.append(
            {
                "part_number": item.get("part_number") or prod.get("part_number"),
                "description": item.get("description") or prod.get("description"),
                "quantity": item.get("quantity"),
                "unit_price": ln.get("price", {}).get("effective_price"),
                "line_total": ln.get("line_total"),
            }
        )
        total += ln.get("line_total") or 0.0

    partial = include_line_indexes is not None and len(saved_lines) < len(all_lines)

    confirmation = {
        "status": "success",
        "order_id": order_id,
        "customer_id": matched.get("id", "UNMAPPED"),
        "customer_name": matched.get("name") or order.get("customer_name"),
        "po_number": po_number,
        "delivery_date": order.get("delivery_date"),
        "line_count": len(saved_lines),
        "line_items": saved_lines,
        "total_amount": round(total, 2),
        "partial": partial,
        "held_line_count": len(all_lines) - len(saved_lines),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": (
            "Partial sales order created in D365 F&O "
            f"({len(all_lines) - len(saved_lines)} line(s) held)"
            if partial
            else "Sales order created successfully in D365 F&O"
        ),
    }

    _save_order(confirmation)
    return confirmation


if __name__ == "__main__":
    demo = {
        "customer": {"matched": {"id": "CUST001", "name": "ABC Medical Inc"}},
        "line_items": [{}, {}],
        "total_amount": 567.50,
    }
    print(json.dumps(create_order(demo, {"po_number": "PO-2026-001"}), indent=2))
    print(f"\nSaved to {ORDERS_DB} ({len(load_orders())} order(s) total)")
