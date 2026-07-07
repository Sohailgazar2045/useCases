"""
confidence.py — Confidence Engine.

Turns the per-field match result into a single order-level confidence score plus
an actionable recommendation for the human reviewer:

    auto_approve : clean order — safe to create as-is
    review       : one or more flags need a human decision
    reject       : nothing usable was matched (no customer, no valid lines)

It also detects the *partial order* case: some lines match the catalog and some
don't, which lets the reviewer create an order for the valid lines while holding
the rest.
"""

from __future__ import annotations

from typing import Any

# A line is "usable" (can go on an order) when its product matched the catalog
# with at least a weak match and it has a quantity.
_MATCHED_STATUSES = {"exact", "fuzzy_strong", "fuzzy_weak"}
# Customer statuses that are strong enough to not, by themselves, force review.
_CUSTOMER_OK = {"exact"}

AUTO_APPROVE_THRESHOLD = 90


def _line_is_usable(line: dict[str, Any]) -> bool:
    product = line.get("product", {})
    return product.get("status") in _MATCHED_STATUSES and not line.get("qty_missing")


def _line_is_clean(line: dict[str, Any]) -> bool:
    """Usable AND with no price/fuzzy caveat — contributes to auto-approve."""
    product = line.get("product", {})
    price = line.get("price", {})
    return (
        product.get("status") == "exact"
        and price.get("status") in ("match", "ok")
        and not line.get("qty_missing")
    )


def score_order(match_result: dict[str, Any]) -> dict[str, Any]:
    """
    Returns:
        {
          overall_confidence: int (0-100),
          recommendation: "auto_approve" | "review" | "reject",
          partial: bool,               # some lines usable, some not
          usable_line_indexes: [int],  # 0-based indexes of createable lines
          reasons: [str],              # human-readable drivers of the decision
        }
    """
    customer = match_result.get("customer", {})
    lines = match_result.get("line_items", [])
    reasons: list[str] = []

    # ---- Component confidences ------------------------------------------- #
    cust_conf = float(customer.get("confidence", 0) or 0)
    line_confs = [float(l.get("product", {}).get("confidence", 0) or 0) for l in lines]
    avg_line_conf = sum(line_confs) / len(line_confs) if line_confs else 0.0

    # Overall = blend of customer and line confidence (customer weighted less,
    # since a name variation is cheaper to resolve than an unknown product).
    if lines:
        overall = round(0.35 * cust_conf + 0.65 * avg_line_conf)
    else:
        overall = round(cust_conf)

    usable_idx = [i for i, l in enumerate(lines) if _line_is_usable(l)]
    usable = len(usable_idx)
    total_lines = len(lines)
    partial = 0 < usable < total_lines

    # ---- Decision drivers ------------------------------------------------ #
    if customer.get("status") not in _CUSTOMER_OK:
        reasons.append(f"Customer not an exact match ({customer.get('status')}).")
    if not lines:
        reasons.append("No line items were extracted.")
    if partial:
        reasons.append(
            f"Partial order: {usable} of {total_lines} lines match the catalog."
        )
    for i, l in enumerate(lines, start=1):
        pstatus = l.get("product", {}).get("status")
        if pstatus == "unknown_product":
            reasons.append(f"Line {i}: unknown product.")
        elif pstatus in ("fuzzy_strong", "fuzzy_weak"):
            reasons.append(f"Line {i}: matched by description, not part number.")
        if l.get("qty_missing"):
            reasons.append(f"Line {i}: missing quantity.")
        if l.get("price", {}).get("status") == "mismatch":
            reasons.append(f"Line {i}: price differs from the master price.")

    # ---- Recommendation -------------------------------------------------- #
    all_clean = bool(lines) and all(_line_is_clean(l) for l in lines)
    customer_clean = customer.get("status") in _CUSTOMER_OK

    if usable == 0:
        # Nothing createable — no valid customer/line to build an order from.
        recommendation = "reject"
    elif all_clean and customer_clean and overall >= AUTO_APPROVE_THRESHOLD:
        recommendation = "auto_approve"
    else:
        recommendation = "review"

    if recommendation == "auto_approve":
        reasons = ["Customer and all line items match the ERP master data."]

    return {
        "overall_confidence": overall,
        "recommendation": recommendation,
        "partial": partial,
        "usable_line_indexes": usable_idx,
        "reasons": reasons,
    }


# Human-friendly labels for the UI.
RECOMMENDATION_LABEL = {
    "auto_approve": "✅ Recommend approve",
    "review": "🟡 Needs review",
    "reject": "🔴 Recommend reject",
}
