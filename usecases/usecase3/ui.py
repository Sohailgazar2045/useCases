"""
ui.py — Streamlit UI for Use Case 3 (AP Invoice Matching).

Thin client over the UC3 FastAPI backend (``api/main.py`` in this package),
same shape as UC1's ui.py. The 3-way match itself is pure Python, so it runs
in-process via ``matcher.match_invoice``; everything that persists (flag / post)
or reads a ledger (review queue / alerts) goes through the API.

Tabs
----
Match Invoice : pick a sample invoice → 3-way match → post (clean) or flag (variance)
Review Queue  : GET /uc3/flagged-invoices → approve+post a pending item
Alerts        : GET /uc3/check-alerts → goods received but not invoiced
"""

from __future__ import annotations

import json
from typing import Any

import requests
import streamlit as st

from .api.runtime import api_is_up, ensure_api_running
from .config import DATA_DIR, UC3_API_BASE_URL
from .matcher import match_invoice

_TIMEOUT = 30  # seconds

_STATUS_EMOJI = {
    "MATCH": "✅",
    "PRICE_VARIANCE": "💲",
    "QTY_VARIANCE": "🔢",
    "NOT_ON_PO": "❓",
    "NOT_RECEIVED": "📦",
}


# --------------------------------------------------------------------------- #
# Data + API helpers
# --------------------------------------------------------------------------- #
@st.cache_data
def _load_samples() -> dict[str, Any]:
    """Load the demo POs / receipts / invoices used by the match panel."""
    with open(DATA_DIR / "samples.json", "r", encoding="utf-8") as fh:
        return json.load(fh)


def _api_error(exc: Exception) -> str:
    if isinstance(exc, requests.ConnectionError):
        return (
            f"Cannot reach the UC3 API at {UC3_API_BASE_URL}. Start it with:\n\n"
            "    uvicorn usecases.usecase3.api.main:app --port 8001"
        )
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        try:
            return exc.response.json().get("detail", str(exc))
        except Exception:  # noqa: BLE001
            return exc.response.text or str(exc)
    return str(exc)


def _get(path: str) -> Any:
    resp = requests.get(f"{UC3_API_BASE_URL}{path}", timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, payload: dict) -> Any:
    resp = requests.post(f"{UC3_API_BASE_URL}{path}", json=payload, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _invoice_total(invoice: dict[str, Any]) -> float:
    total = 0.0
    for ln in invoice.get("line_items", []):
        total += (ln.get("quantity") or 0) * (ln.get("unit_price") or 0)
    return round(total, 2)


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
def _match_tab() -> None:
    samples = _load_samples()
    invoices = samples["invoices"]

    labels = [
        f"{inv['invoice_number']} — {inv['vendor']} ({inv['po_number']})" for inv in invoices
    ]
    choice = st.selectbox(
        "Select a supplier invoice", range(len(invoices)), format_func=lambda i: labels[i]
    )
    invoice = invoices[choice]
    total = _invoice_total(invoice)

    st.caption(f"Invoice total: **${total:,.2f}** · {len(invoice['line_items'])} line(s)")

    match = match_invoice(invoice, samples["purchase_orders"], samples["receipts"])

    # Overall verdict
    if match["match_result"] == "PERFECT_MATCH":
        st.success("✅ PERFECT_MATCH — 3-way match clean, eligible for auto-post.")
    else:
        st.warning("⚠️ VARIANCE_FOUND — needs review before posting.")

    # Per-line results
    rows = [
        {
            "": _STATUS_EMOJI.get(ln["status"], ""),
            "Part": ln["part_number"],
            "Description": ln.get("description"),
            "Inv Qty": ln["invoice_qty"],
            "Recv Qty": ln["received_qty"],
            "Inv Price": ln["invoice_unit_price"],
            "PO Price": ln["po_unit_price"],
            "Status": ln["status"],
        }
        for ln in match["line_results"]
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    if match["flags"]:
        with st.expander("Flags", expanded=True):
            for f in match["flags"]:
                st.write(f"- {f}")

    st.divider()
    approved_by = st.text_input(
        "Approver", value=st.session_state.get("uc3_approver", "demo.user")
    )
    st.session_state["uc3_approver"] = approved_by

    match_summary = {"match_result": match["match_result"], "flags": match["flags"]}

    col_post, col_flag = st.columns(2)
    with col_post:
        if st.button(
            "💸 Approve & Post",
            type="primary",
            use_container_width=True,
            disabled=not approved_by.strip(),
        ):
            try:
                out = _post(
                    "/uc3/post-invoice",
                    {
                        "invoice_number": invoice["invoice_number"],
                        "vendor": invoice["vendor"],
                        "po_number": invoice["po_number"],
                        "invoice_amount": total,
                        "match_result": match_summary,
                        "status": "Posted",
                        "approved_by": approved_by.strip(),
                    },
                )
                st.success(f"Posted. Payment reference **{out['payment_ref']}**.")
            except Exception as exc:  # noqa: BLE001
                st.error(_api_error(exc))
    with col_flag:
        if st.button("🚩 Flag for Review", use_container_width=True):
            try:
                _post(
                    "/uc3/flag-invoice",
                    {
                        "invoice_number": invoice["invoice_number"],
                        "vendor_name": invoice["vendor"],
                        "po_number": invoice["po_number"],
                        "total_amount": total,
                        "match_result": match_summary,
                        "status": "Pending Review",
                    },
                )
                st.success(f"{invoice['invoice_number']} flagged for review.")
            except Exception as exc:  # noqa: BLE001
                st.error(_api_error(exc))


def _review_tab() -> None:
    try:
        flagged = _get("/uc3/flagged-invoices")
    except Exception as exc:  # noqa: BLE001
        st.error(_api_error(exc))
        return

    if not flagged:
        st.info("No flagged invoices. Match an invoice with a variance to populate this queue.")
        return

    st.dataframe(flagged, use_container_width=True, hide_index=True)

    pending = [f for f in flagged if f.get("status") == "Pending Review"]
    if not pending:
        st.caption("Nothing pending — all flagged invoices have been resolved.")
        return

    st.divider()
    st.subheader("Approve a pending invoice")
    labels = [
        f"{p['invoice_number']} — {p.get('vendor_name')} (${(p.get('total_amount') or 0):,.2f})"
        for p in pending
    ]
    idx = st.selectbox("Pending invoice", range(len(pending)), format_func=lambda i: labels[i])
    chosen = pending[idx]
    approver = st.text_input(
        "Approver", value=st.session_state.get("uc3_approver", "demo.user"), key="review_approver"
    )

    if st.button("💸 Approve & Post", type="primary", disabled=not approver.strip()):
        try:
            out = _post(
                "/uc3/post-invoice",
                {
                    "invoice_number": chosen["invoice_number"],
                    "vendor": chosen.get("vendor_name"),
                    "po_number": chosen.get("po_number"),
                    "invoice_amount": chosen.get("total_amount"),
                    "match_result": chosen.get("match_result"),
                    "status": "Posted",
                    "approved_by": approver.strip(),
                },
            )
            st.success(f"Posted. Payment reference **{out['payment_ref']}**.")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(_api_error(exc))


def _alerts_tab() -> None:
    try:
        alerts = _get("/uc3/check-alerts")
    except Exception as exc:  # noqa: BLE001
        st.error(_api_error(exc))
        return

    if not alerts:
        st.success("No open alerts — every received PO has a posted invoice.")
        return

    st.warning(f"{len(alerts)} goods receipt(s) received but not invoiced (≥ 30 days).")
    st.dataframe(alerts, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def render() -> None:
    st.title("🧾 AP Invoice Matching")
    st.caption(
        "3-way match supplier invoices against POs and goods receipts → auto-post or route to review."
    )

    # Make sure the backend is up (idempotent — Home.py starts it too). A remote
    # API (UC3_API_BASE_URL set to a Render/etc. URL) may be waking from idle, so
    # allow enough time for a free-tier cold start on the first request.
    if not api_is_up():
        with st.spinner("Waking the AP Matching API… (first request after idle can take ~30–50s)"):
            ensure_api_running(timeout=75)
    if not api_is_up():
        st.error(
            f"The UC3 API is not reachable at {UC3_API_BASE_URL}. "
            "Start it with: `uvicorn usecases.usecase3.api.main:app --port 8001`"
        )
        return

    tab_match, tab_review, tab_alerts = st.tabs(
        ["🔍 Match Invoice", "🚩 Review Queue", "🔔 Alerts"]
    )
    with tab_match:
        _match_tab()
    with tab_review:
        _review_tab()
    with tab_alerts:
        _alerts_tab()
