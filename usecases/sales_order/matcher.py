"""
matcher.py — Match extracted order data against hardcoded master data.

Produces per-field match results with confidence scores and human-readable
flags that the UI can render.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from rapidfuzz import fuzz

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Confidence thresholds (percent of fuzzy string similarity).
STRONG_MATCH = 80
WEAK_MATCH = 50

# Common company suffixes treated as equivalent / ignorable when comparing names,
# so "ABC Medical Inc." == "ABC Medical Inc" == "ABC Medical Incorporated".
_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "co", "corp", "corporation",
    "company", "plc", "gmbh", "sa", "llp",
}


def _normalize(text: str | None) -> str:
    """
    Canonicalize a name/description for comparison:
    lowercase, drop punctuation, collapse whitespace, and strip trailing
    company suffixes. Trivial differences (a stray '.', casing, double spaces)
    then compare as identical.
    """
    if not text:
        return ""
    # lowercase, replace any non-alphanumeric run with a single space
    cleaned = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    tokens = [t for t in cleaned.split() if t]
    # drop trailing company suffix tokens (e.g. "inc", "ltd")
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _load(name: str) -> list[dict[str, Any]]:
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_customers() -> list[dict[str, Any]]:
    return _load("customers.json")


def load_products() -> list[dict[str, Any]]:
    return _load("products.json")


def load_pricing() -> dict[str, dict[str, Any]]:
    """Return a {part_number: {unit_price, currency}} map from pricing.json.

    Pricing lives in its own file (the ERP price list) rather than on the
    product record, mirroring how a real D365 item master and trade agreements
    are kept separate.
    """
    rows = _load("pricing.json")
    return {r["part_number"]: r for r in rows}


# --------------------------------------------------------------------------- #
# Customer matching
# --------------------------------------------------------------------------- #
def match_customer(name: str | None, customers: list[dict[str, Any]]) -> dict[str, Any]:
    """
    exact match        -> confidence 100
    fuzzy > 80         -> confidence = score  (strong)
    fuzzy 50-80        -> confidence = score  + flag for review
    no match           -> confidence 0        + flag as new customer
    """
    result: dict[str, Any] = {
        "input_name": name,
        "matched": None,
        "confidence": 0,
        "status": "new_customer",
        "flag": None,
    }
    if not name:
        result["flag"] = "No customer name extracted"
        return result

    # 1) Normalized exact match — ignores trivial differences (trailing '.',
    #    casing, extra spaces, company suffix). "ABC Medical Inc." == master.
    norm_name = _normalize(name)
    for cust in customers:
        if norm_name and norm_name == _normalize(cust["name"]):
            result.update(matched=cust, confidence=100, status="exact")
            return result

    # 2) Otherwise fall back to fuzzy similarity.
    best, best_score = None, 0.0
    for cust in customers:
        score = fuzz.token_sort_ratio(norm_name, _normalize(cust["name"]))
        if score > best_score:
            best, best_score = cust, score

    if best is None:
        result["flag"] = f"Unknown customer '{name}' — flag as new customer"
        return result

    if best_score >= 99.5:  # treat as exact
        result.update(matched=best, confidence=100, status="exact")
    elif best_score >= STRONG_MATCH:
        result.update(
            matched=best,
            confidence=round(best_score),
            status="fuzzy_strong",
            flag=f"Customer name variation: '{name}' → '{best['name']}'",
        )
    elif best_score >= WEAK_MATCH:
        result.update(
            matched=best,
            confidence=round(best_score),
            status="fuzzy_weak",
            flag=f"Low-confidence customer match: '{name}' → '{best['name']}' "
            f"({round(best_score)}%) — review",
        )
    else:
        result.update(
            confidence=0,
            status="new_customer",
            flag=f"Unknown customer '{name}' — flag as new customer",
        )
    return result


# --------------------------------------------------------------------------- #
# Product matching
# --------------------------------------------------------------------------- #
def match_product(item: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
    """
    part number exact         -> confidence 100
    description fuzzy > 80     -> confidence = score (strong)
    description fuzzy 50-80    -> confidence = score + flag
    no match                  -> flag as unknown product
    """
    part_number = item.get("part_number")
    description = item.get("description")

    result: dict[str, Any] = {
        "matched": None,
        "confidence": 0,
        "status": "unknown_product",
        "flag": None,
        "match_basis": None,
    }

    # 1) Exact part-number match wins.
    if part_number:
        for prod in products:
            if prod["part_number"].lower() == str(part_number).lower():
                result.update(
                    matched=prod,
                    confidence=100,
                    status="exact",
                    match_basis="part_number",
                )
                return result

    # 2) Fall back to description match (normalized exact first, then fuzzy).
    if description:
        norm_desc = _normalize(description)
        for prod in products:
            if norm_desc and norm_desc == _normalize(prod["description"]):
                result.update(
                    matched=prod,
                    confidence=100,
                    status="exact",
                    match_basis="description",
                )
                return result

        best, best_score = None, 0.0
        for prod in products:
            score = fuzz.token_sort_ratio(norm_desc, _normalize(prod["description"]))
            if score > best_score:
                best, best_score = prod, score

        if best is not None and best_score >= STRONG_MATCH:
            result.update(
                matched=best,
                confidence=round(best_score),
                status="fuzzy_strong",
                match_basis="description",
                flag=f"Matched by description: '{description}' → "
                f"{best['part_number']} ({round(best_score)}%)",
            )
            return result
        if best is not None and best_score >= WEAK_MATCH:
            result.update(
                matched=best,
                confidence=round(best_score),
                status="fuzzy_weak",
                match_basis="description",
                flag=f"Low-confidence product match: '{description}' → "
                f"{best['part_number']} ({round(best_score)}%) — review",
            )
            return result

    label = part_number or description or "unnamed item"
    result["flag"] = f"Unknown product '{label}' — not in catalog"
    return result


# --------------------------------------------------------------------------- #
# Price validation
# --------------------------------------------------------------------------- #
def validate_price(
    item: dict[str, Any],
    product_match: dict[str, Any],
    pricing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    extracted == master   -> ok
    extracted != master   -> flag + difference
    not extracted         -> use master price + note

    The master price is looked up in the ERP price list (pricing.json) by the
    matched product's part number.
    """
    pricing = pricing if pricing is not None else load_pricing()
    master = product_match.get("matched")
    extracted = item.get("unit_price")

    master_price = None
    if master is not None:
        price_row = pricing.get(master.get("part_number"))
        master_price = price_row["unit_price"] if price_row else None

    out: dict[str, Any] = {
        "extracted_price": extracted,
        "master_price": master_price,
        "effective_price": extracted,
        "status": "ok",
        "flag": None,
    }

    # No matched product, or matched but absent from the price list.
    if master is None or master_price is None:
        out["status"] = "no_master"
        out["effective_price"] = extracted
        if master is not None and master_price is None:
            out["flag"] = (
                f"No price on file for {master.get('part_number')} — review pricing"
            )
        return out

    # Treat a missing OR zero price as "not extracted" — the extraction model
    # tends to emit 0.0 rather than null when no price appears in the document.
    if not extracted:
        out.update(
            effective_price=master_price,
            status="used_master",
            flag=f"No price extracted — using master price ${master_price:,.2f}",
        )
        return out

    if abs(extracted - master_price) < 0.005:
        out["status"] = "match"
        out["effective_price"] = extracted
        return out

    diff = extracted - master_price
    out.update(
        status="mismatch",
        effective_price=extracted,
        flag=f"Price mismatch: PO ${extracted:,.2f} vs master ${master_price:,.2f} "
        f"(diff ${diff:+,.2f})",
    )
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def match_order(order: dict[str, Any]) -> dict[str, Any]:
    """
    Returns:
        {
          customer: <customer match>,
          line_items: [ {item, product, price, line_total}, ... ],
          flags: [str, ...],
          total_amount: float,
        }
    """
    customers = load_customers()
    products = load_products()
    pricing = load_pricing()

    customer = match_customer(order.get("customer_name"), customers)

    lines: list[dict[str, Any]] = []
    flags: list[str] = []
    total = 0.0

    if customer.get("flag"):
        flags.append("⚠️ " + customer["flag"])

    if not order.get("line_items"):
        flags.append("⚠️ No line items extracted — low-confidence extraction")

    for idx, item in enumerate(order.get("line_items", []), start=1):
        product = match_product(item, products)
        price = validate_price(item, product, pricing)

        raw_qty = item.get("quantity")
        qty_missing = not raw_qty or raw_qty <= 0
        qty = raw_qty or 0
        eff = price.get("effective_price")
        line_total = (eff or 0) * qty
        total += line_total

        if product.get("flag"):
            flags.append(f"⚠️ Line {idx}: {product['flag']}")
        if qty_missing:
            flags.append(f"⚠️ Line {idx}: missing or zero quantity — confirm with customer")
        if price.get("flag"):
            flags.append(f"⚠️ Line {idx}: {price['flag']}")

        lines.append(
            {
                "item": item,
                "product": product,
                "price": price,
                "line_total": line_total,
                "qty_missing": qty_missing,
            }
        )

    return {
        "customer": customer,
        "line_items": lines,
        "flags": flags,
        "total_amount": round(total, 2),
    }


if __name__ == "__main__":
    import sys

    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        sample = json.load(fh)
    print(json.dumps(match_order(sample), indent=2))
