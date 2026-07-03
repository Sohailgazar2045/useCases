"""
app.py — Streamlit UI for the PO-to-D365 demo.

Left panel : uploaded PO rendered as an image.
Right panel: extracted + matched data, flags, and approval actions.

Run:  streamlit run app.py
"""

from __future__ import annotations

import os

import streamlit as st

from extractor import (
    extract_order,
    extract_order_from_image,
    extract_order_from_text,
)
from matcher import match_order
from order_creator import ORDERS_DB, create_order, load_orders

st.set_page_config(page_title="PO → D365 Demo", layout="wide")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def render_pdf_first_page(pdf_bytes: bytes) -> bytes | None:
    """Render page 1 to PNG bytes with PyMuPDF for the left preview panel."""
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
# Session state
# --------------------------------------------------------------------------- #
if "order" not in st.session_state:
    st.session_state.order = None
    st.session_state.match = None
    st.session_state.created = None
    # Preview of whatever the user submitted:
    st.session_state.preview_kind = None   # "pdf" | "image" | "text"
    st.session_state.preview_bytes = None  # for pdf / image
    st.session_state.preview_text = None   # for email text


# --------------------------------------------------------------------------- #
# Sidebar — input
# --------------------------------------------------------------------------- #
st.sidebar.title("📄 Purchase Order Intake")
st.sidebar.caption("AI extraction → master-data match → mock D365 order")

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
else:  # Scanned / handwritten image
    uploaded = st.sidebar.file_uploader(
        "Upload a PO image", type=["png", "jpg", "jpeg", "webp"]
    )

process = st.sidebar.button("🔍 Extract & Match", type="primary", use_container_width=True)

if st.sidebar.button("🔄 Reset", use_container_width=True):
    for k in ("order", "match", "created", "preview_kind", "preview_bytes", "preview_text"):
        st.session_state[k] = None
    st.rerun()

# ---- Saved orders (persisted to orders.json) --------------------------- #
st.sidebar.divider()
_saved = load_orders()
st.sidebar.markdown(f"### 🗄️ Created Orders ({len(_saved)})")
if _saved:
    for o in reversed(_saved[-10:]):  # most recent first
        st.sidebar.caption(
            f"`{o['order_id']}` · {o.get('customer_name', '—')} · "
            f"${o.get('total_amount', 0):,.2f}"
        )
    st.sidebar.download_button(
        "⬇️ Download orders.json",
        data=open(ORDERS_DB, "rb").read(),
        file_name="orders.json",
        mime="application/json",
        use_container_width=True,
    )
else:
    st.sidebar.caption("None yet — approve an order to save one.")


# --------------------------------------------------------------------------- #
# Processing
# --------------------------------------------------------------------------- #
if process:
    # Resolve the chosen input into (extractor callable, preview payload).
    extract_fn = None
    payload = None
    error = None

    if source_kind == "PDF attachment":
        if uploaded is None:
            error = "Upload a PDF first."
        else:
            data = uploaded.getvalue()
            extract_fn = lambda: extract_order(data)
            st.session_state.preview_kind = "pdf"
            st.session_state.preview_bytes = data
    elif source_kind == "Typed email body":
        if not email_text.strip():
            error = "Paste the order email text first."
        else:
            text = email_text
            extract_fn = lambda: extract_order_from_text(text)
            st.session_state.preview_kind = "text"
            st.session_state.preview_text = text
    else:  # Scanned / handwritten image
        if uploaded is None:
            error = "Upload an image first."
        else:
            data = uploaded.getvalue()
            extract_fn = lambda: extract_order_from_image(data)
            st.session_state.preview_kind = "image"
            st.session_state.preview_bytes = data

    if error:
        st.sidebar.error(error)
    else:
        st.session_state.created = None
        try:
            with st.spinner("Extracting with OpenAI…"):
                order = extract_fn()
            with st.spinner("Matching against master data…"):
                match = match_order(order)
            st.session_state.order = order
            st.session_state.match = match
        except Exception as exc:  # noqa: BLE001 — surface any failure in UI
            st.session_state.order = None
            st.session_state.match = None
            st.error(f"Processing failed: {exc}")


# --------------------------------------------------------------------------- #
# Main layout
# --------------------------------------------------------------------------- #
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

    if not order or not match:
        st.info("Results will appear here after processing.")
    else:
        cust = match["customer"]
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
        st.caption(f"Extraction source: {_labels.get(order.get('_source'), order.get('_source'))}")

        # ---- Line items table ------------------------------------------- #
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

        # ---- Flags ------------------------------------------------------ #
        if match["flags"]:
            st.markdown("**Flags for Review**")
            for f in match["flags"]:
                st.error(f)
        else:
            st.success("✅ No flags — clean order, ready to approve.")

        st.divider()

        # ---- Actions ---------------------------------------------------- #
        if st.session_state.created is None:
            a1, a2, a3 = st.columns(3)
            with a1:
                approve = st.button("✅ Approve Order", type="primary", use_container_width=True)
            with a2:
                st.button("✏️ Edit Fields", use_container_width=True, disabled=True,
                          help="Inline editing — stub for demo")
            with a3:
                reject = st.button("❌ Reject Order", use_container_width=True)

            if approve:
                st.session_state.created = create_order(match, order)
                st.rerun()
            if reject:
                st.warning("Order rejected. Nothing was sent to D365.")
        else:
            res = st.session_state.created
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
            st.caption(res["message"])
            st.caption(f"💾 Saved to `{os.path.basename(ORDERS_DB)}`")
            st.json(res, expanded=False)
