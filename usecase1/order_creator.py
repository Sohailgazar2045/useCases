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


def create_order(match_result: dict[str, Any], order: dict[str, Any]) -> dict[str, Any]:
    """
    Receives the approved match result + original extracted order, generates a
    mock D365 confirmation, PERSISTS it to orders.json, and returns it.
    """
    customer = match_result.get("customer", {})
    matched = customer.get("matched") or {}

    existing = load_orders()
    order_id = _next_order_id(existing)

    # Flatten the approved line items so the saved record is self-contained.
    saved_lines = []
    for ln in match_result.get("line_items", []):
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

    confirmation = {
        "status": "success",
        "order_id": order_id,
        "customer_id": matched.get("id", "UNMAPPED"),
        "customer_name": matched.get("name") or order.get("customer_name"),
        "po_number": order.get("po_number"),
        "delivery_date": order.get("delivery_date"),
        "line_count": len(saved_lines),
        "line_items": saved_lines,
        "total_amount": match_result.get("total_amount", 0.0),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": "Sales order created successfully in D365 F&O",
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
