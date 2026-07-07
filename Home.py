"""
Home.py — Landing hub for the use-case suite.

Run:  streamlit run Home.py

Streamlit automatically turns every file in ``pages/`` into a sidebar nav entry.
This page is the entry point and lists the available use cases.
"""

from __future__ import annotations

import streamlit as st

from usecases import USE_CASES

st.set_page_config(page_title="Use Case Suite", page_icon="🗂️", layout="wide")

st.title("🗂️ Use Case Suite")
st.caption("A collection of AI-assisted business automation demos. Pick one to begin.")

st.divider()

# Page files, in nav order, so each card can deep-link to its use case.
_PAGE_PATHS = {
    "sales_order": "pages/1_Sales_Order_Entry.py",
    "usecase2": "pages/2_Use_Case_2.py",
    "usecase3": "pages/3_Use_Case_3.py",
}

_STATUS_BADGE = {"live": "🟢 Live", "in_progress": "🟡 In progress", "planned": "⚪ Planned"}

cols = st.columns(len(USE_CASES))
for col, uc in zip(cols, USE_CASES):
    with col:
        st.subheader(f"{uc['icon']} {uc['number']}. {uc['title']}")
        st.caption(uc["tagline"])
        st.write(_STATUS_BADGE.get(uc["status"], uc["status"]))
        page = _PAGE_PATHS.get(uc["key"])
        if page:
            st.page_link(page, label="Open →")

st.divider()
st.caption(
    "Add a new use case: create `usecases/<name>/ui.py` with a `render()` function, "
    "register it in `usecases/__init__.py`, and add a wrapper in `pages/`."
)
