"""
ui.py — Streamlit UI for the PO-to-D365 use case.

This is a thin client over the FastAPI backend (``api/main.py`` in this package):
it uploads the
document to ``/process`` (extract + match + confidence) and approves via
``/orders``. No pipeline logic lives here.

Left panel : uploaded PO rendered as an image / text.
Right panel: extracted + matched data, confidence + AI recommendation, actions.
"""

from __future__ import annotations

import json

import requests
import streamlit as st

from shared.config import API_BASE_URL
from .api.runtime import api_is_up, ensure_api_running
from .confidence import RECOMMENDATION_LABEL

_TIMEOUT = 120  # seconds — extraction can take a while on vision


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #
def _api_error(exc: Exception) -> str:
    if isinstance(exc, requests.ConnectionError):
        return (
            f"Cannot reach the API at {API_BASE_URL}. Start it with:\n\n"
            "    uvicorn usecases.sales_order.api.main:app --port 8000"
        )
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        try:
            return exc.response.json().get("detail", str(exc))
        except Exception:  # noqa: BLE001
            return exc.response.text or str(exc)
    return str(exc)


def api_process(source_kind: str, *, data: bytes | None = None,
                filename: str | None = None, mime: str | None = None,
                text: str | None = None) -> dict:
    """POST to /process and return {order, match, confidence}."""
    url = f"{API_BASE_URL}/process"
    if source_kind == "text":
        resp = requests.post(url, data={"source_kind": "text", "text": text}, timeout=_TIMEOUT)
    else:
        files = {"file": (filename, data, mime)}
        resp = requests.post(
            url, data={"source_kind": source_kind}, files=files, timeout=_TIMEOUT
        )
    resp.raise_for_status()
    return resp.json()


def api_create_order(order: dict, match: dict, include_line_indexes=None) -> dict:
    resp = requests.post(
        f"{API_BASE_URL}/orders",
        json={"order": order, "match": match, "include_line_indexes": include_line_indexes},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def api_list_orders() -> list[dict]:
    try:
        resp = requests.get(f"{API_BASE_URL}/orders", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001 — sidebar list is best-effort
        return []


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #
def render_pdf_first_page(pdf_bytes: bytes) -> bytes | None:
    try:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pix = doc.load_page(0).get_pixmap(dpi=150)
        return pix.tobytes("png")
    except Exception as exc:  # noqa: BLE001 — preview is best-effort
        st.warning(f"Could not render PDF preview: {exc}")
        return None


def confidence_badge(status: str, confidence: int) -> str:
    color = {
        "exact": "🟢",
        "fuzzy_strong": "🟡",
        "fuzzy_weak": "🟠",
        "new_customer": "🔴",
        "unknown_product": "🔴",
    }.get(status, "⚪")
    return f"{color} {confidence}%"


def line_status_label(product: dict, price: dict) -> str:
    if product["status"] in ("unknown_product",):
        return "❌ Unknown"
    if price["status"] == "mismatch":
        return "⚠️ Price diff"
    if product["status"] in ("fuzzy_strong", "fuzzy_weak"):
        return "🟡 Fuzzy"
    if price["status"] == "used_master":
        return "ℹ️ Master price"
    return "✅ Match"


def money(v) -> str:
    return "N/A" if v is None else f"${v:,.2f}"


# --------------------------------------------------------------------------- #
# UI entry point
# --------------------------------------------------------------------------- #
def render() -> None:
    # Single-command experience: bring the backend up automatically if it isn't
    # already running (a manually-started API is detected and reused).
    if not api_is_up():
        with st.spinner("Starting backend API…"):
            started = ensure_api_running()
        if not started:
            st.error(
                f"Could not reach or start the backend API at {API_BASE_URL}.\n\n"
                "Start it manually with:\n\n"
                "    uvicorn usecases.sales_order.api.main:app --port 8000"
            )
            st.stop()

    if "order" not in st.session_state:
        st.session_state.order = None
        st.session_state.match = None
        st.session_state.confidence = None
        st.session_state.created = None
        st.session_state.auto_created = False   # created via straight-through?
        st.session_state.preview_kind = None   # "pdf" | "image" | "text"
        st.session_state.preview_bytes = None
        st.session_state.preview_text = None

    # ----------------------------------------------------------------------- #
    # Sidebar — input
    # ----------------------------------------------------------------------- #
    st.sidebar.title("📄 Purchase Order Intake")
    st.sidebar.caption(
        "LangChain extraction → master-data match → confidence + AI recommendation → mock D365"
    )

    source_kind = st.sidebar.radio(
        "Order source",
        ["PDF attachment", "Typed email body", "Scanned / handwritten image"],
        help="The same AI pipeline handles all three formats.",
    )

    uploaded = None
    email_text = ""
    if source_kind == "PDF attachment":
        uploaded = st.sidebar.file_uploader("Upload a PO (PDF)", type=["pdf"])
    elif source_kind == "Typed email body":
        email_text = st.sidebar.text_area(
            "Paste the order email text",
            height=220,
            placeholder=(
                "e.g.\nHi, please process the following order for ABC Medical Inc...\n"
                "MED-1001 Surgical Gloves Box x10 @ $45\n..."
            ),
        )
    else:
        uploaded = st.sidebar.file_uploader(
            "Upload a PO image", type=["png", "jpg", "jpeg", "webp"]
        )

    process = st.sidebar.button(
        "🔍 Extract & Match", type="primary", use_container_width=True
    )

    auto_create = st.sidebar.checkbox(
        "⚡ Auto-create on 100% match",
        value=True,
        help="Straight-through processing: a perfect match (exact customer, every "
        "line exact, prices match, no flags) is created without manual approval. "
        "Anything less still routes to human review.",
    )

    if st.sidebar.button("🔄 Reset", use_container_width=True):
        for k in ("order", "match", "confidence", "created",
                  "preview_kind", "preview_bytes", "preview_text"):
            st.session_state[k] = None
        st.session_state.auto_created = False
        st.rerun()

    # ---- Saved orders (from GET /orders) ---------------------------------- #
    st.sidebar.divider()
    _saved = api_list_orders()
    st.sidebar.markdown(f"### 🗄️ Created Orders ({len(_saved)})")
    if _saved:
        for o in reversed(_saved[-10:]):
            st.sidebar.caption(
                f"`{o['order_id']}` · {o.get('customer_name', '—')} · "
                f"${o.get('total_amount', 0):,.2f}"
            )
        st.sidebar.download_button(
            "⬇️ Download orders.json",
            data=json.dumps(_saved, indent=2).encode("utf-8"),
            file_name="orders.json",
            mime="application/json",
            use_container_width=True,
        )
    else:
        st.sidebar.caption("None yet — approve an order to save one.")

    # ----------------------------------------------------------------------- #
    # Processing (calls the API)
    # ----------------------------------------------------------------------- #
    if process:
        error = None
        call = None

        if source_kind == "PDF attachment":
            if uploaded is None:
                error = "Upload a PDF first."
            else:
                data = uploaded.getvalue()
                st.session_state.preview_kind = "pdf"
                st.session_state.preview_bytes = data
                call = lambda: api_process(
                    "pdf", data=data, filename=uploaded.name, mime="application/pdf"
                )
        elif source_kind == "Typed email body":
            if not email_text.strip():
                error = "Paste the order email text first."
            else:
                text = email_text
                st.session_state.preview_kind = "text"
                st.session_state.preview_text = text
                call = lambda: api_process("text", text=text)
        else:
            if uploaded is None:
                error = "Upload an image first."
            else:
                data = uploaded.getvalue()
                st.session_state.preview_kind = "image"
                st.session_state.preview_bytes = data
                call = lambda: api_process(
                    "image", data=data, filename=uploaded.name,
                    mime=uploaded.type or "image/png",
                )

        if error:
            st.sidebar.error(error)
        else:
            st.session_state.created = None
            st.session_state.auto_created = False
            try:
                with st.spinner("Processing via API (OpenAI extraction + match)…"):
                    result = call()
                order = result["order"]
                match = result["match"]
                confidence = result["confidence"]
                st.session_state.order = order
                st.session_state.match = match
                st.session_state.confidence = confidence

                # Straight-through: a 100% match (recommendation == auto_approve)
                # is created immediately, skipping manual approval.
                if auto_create and confidence.get("recommendation") == "auto_approve":
                    with st.spinner("100% match — auto-creating order…"):
                        st.session_state.created = api_create_order(order, match)
                    st.session_state.auto_created = True
            except Exception as exc:  # noqa: BLE001
                st.session_state.order = None
                st.session_state.match = None
                st.session_state.confidence = None
                st.error(f"Processing failed: {_api_error(exc)}")

    # ----------------------------------------------------------------------- #
    # Main layout
    # ----------------------------------------------------------------------- #
    st.title("Purchase Order → Dynamics 365 F&O")

    left, right = st.columns([1, 1.3], gap="large")

    with left:
        st.subheader("Original Document")
        kind = st.session_state.preview_kind
        if kind == "pdf":
            png = render_pdf_first_page(st.session_state.preview_bytes)
            if png:
                st.image(png, use_container_width=True)
            st.download_button(
                "Download source PDF",
                data=st.session_state.preview_bytes,
                file_name="purchase_order.pdf",
                mime="application/pdf",
            )
        elif kind == "image":
            st.image(st.session_state.preview_bytes, use_container_width=True)
        elif kind == "text":
            st.text_area(
                "Email body", st.session_state.preview_text, height=420, disabled=True
            )
        else:
            st.info("Choose an order source, provide it, then click **Extract & Match**.")

    with right:
        st.subheader("Extracted & Matched Data")
        order = st.session_state.order
        match = st.session_state.match
        confidence = st.session_state.confidence

        if not order or not match:
            st.info("Results will appear here after processing.")
        else:
            _render_results(order, match, confidence)


# --------------------------------------------------------------------------- #
# Results panel
# --------------------------------------------------------------------------- #
def _render_results(order: dict, match: dict, confidence: dict | None) -> None:
    cust = match["customer"]

    # ---- Confidence + AI recommendation ----------------------------------- #
    if confidence:
        rec = confidence.get("recommendation", "review")
        cc1, cc2 = st.columns([1, 2])
        with cc1:
            st.metric("Confidence", f"{confidence.get('overall_confidence', 0)}%")
        with cc2:
            st.markdown("**AI Recommendation**")
            st.markdown(f"### {RECOMMENDATION_LABEL.get(rec, rec)}")
        if confidence.get("reasons"):
            with st.expander("Why?", expanded=(rec != "auto_approve")):
                for r in confidence["reasons"]:
                    st.write("• " + r)
        st.divider()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Customer**")
        st.write(order.get("customer_name") or "—")
        st.caption(
            f"Match: {confidence_badge(cust['status'], cust['confidence'])}"
            + (f" → {cust['matched']['name']}" if cust.get("matched") else "")
        )
    with c2:
        st.markdown("**PO Number**")
        st.write(order.get("po_number") or "—")
        st.markdown("**Delivery Date**")
        st.write(order.get("delivery_date") or "—")

    st.markdown("**Ship To**")
    st.write(order.get("shipping_address") or "—")
    _labels = {
        "pdf_text": "PDF (text)",
        "pdf_scanned": "PDF (scanned → vision)",
        "email_text": "Typed email body",
        "image": "Image (scanned / handwritten → vision)",
    }
    st.caption(
        f"Extraction source: {_labels.get(order.get('_source'), order.get('_source'))}"
    )

    # ---- Line items table ------------------------------------------------- #
    st.markdown("**Line Items**")
    rows = []
    for ln in match["line_items"]:
        item = ln["item"]
        product = ln["product"]
        price = ln["price"]
        matched = product.get("matched") or {}
        rows.append(
            {
                "Part No": item.get("part_number")
                or (matched.get("part_number") if matched else None)
                or "—",
                "Description": item.get("description")
                or (matched.get("description") if matched else "—"),
                "Qty": item.get("quantity"),
                "PO Price": money(price.get("extracted_price")),
                "Master Price": money(price.get("master_price")),
                "Line Total": money(ln.get("line_total")),
                "Status": line_status_label(product, price),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.metric("Order Total", money(match["total_amount"]))

    # ---- Flags ------------------------------------------------------------ #
    if match["flags"]:
        st.markdown("**Flags for Review**")
        for f in match["flags"]:
            st.error(f)
    else:
        st.success("✅ No flags — clean order, ready to approve.")

    st.divider()

    # ---- Actions ---------------------------------------------------------- #
    if st.session_state.created is None:
        _render_actions(order, match, confidence)
    else:
        _render_confirmation(st.session_state.created)


def _render_actions(order: dict, match: dict, confidence: dict | None) -> None:
    partial = bool(confidence and confidence.get("partial"))
    usable = (confidence or {}).get("usable_line_indexes")

    include_idx = None
    if partial:
        st.info(
            f"Partial order: {len(usable)} of {len(match['line_items'])} lines match "
            "the catalog. You can create just the matched lines and hold the rest."
        )
        only_valid = st.checkbox(
            "Create matched lines only (hold unknown lines)", value=True
        )
        if only_valid:
            include_idx = usable

    a1, a2, a3 = st.columns(3)
    with a1:
        approve = st.button("✅ Approve Order", type="primary", use_container_width=True)
    with a2:
        st.button("✏️ Edit Fields", use_container_width=True, disabled=True,
                  help="Inline editing — stub for demo")
    with a3:
        reject = st.button("❌ Reject Order", use_container_width=True)

    if approve:
        try:
            with st.spinner("Creating order via API…"):
                st.session_state.created = api_create_order(order, match, include_idx)
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Order creation failed: {_api_error(exc)}")
    if reject:
        st.warning("Order rejected. Nothing was sent to D365.")


def _render_confirmation(res: dict) -> None:
    if res.get("status") == "duplicate":
        st.warning("⚠️ Duplicate PO — no new order created")
    elif st.session_state.get("auto_created"):
        st.success("⚡ Order Auto-Created (100% match — straight-through, no review needed)")
    elif res.get("partial"):
        st.success("✅ Partial Order Created")
    else:
        st.success("✅ Order Created Successfully")

    st.markdown(
        f"""
        **Order ID:** `{res['order_id']}`
        **Customer:** {res['customer_name']}  (`{res['customer_id']}`)
        **PO Number:** {res['po_number']}
        **Total:** {money(res['total_amount'])}
        **Created:** {res['created_at']}
        """
    )
    if res.get("held_line_count"):
        st.caption(f"⏸️ {res['held_line_count']} line(s) held for follow-up.")
    st.caption(res["message"])
    st.json(res, expanded=False)
