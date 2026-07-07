"""Hub page wrapper for Use Case 2 — Cash Application."""

import streamlit as st

st.set_page_config(page_title="Cash Application", page_icon="💵", layout="wide")

from usecases.usecase2.ui import render

render()
