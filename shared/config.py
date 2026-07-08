"""
shared/config.py — Configuration and the OpenAI client shared by every use case.

Loads the single project-level .env once and hands out a configured client so
individual use cases don't each re-implement key handling.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Base URL of the FastAPI backend the Streamlit UI talks to.
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


def get_openai_api_key() -> str:
    """Return the OpenAI API key, or raise a clear error if it's missing.

    Resolution order (so it works locally and on Streamlit Cloud regardless of
    how the secret is provided):
      1. ``OPENAI_API_KEY`` environment variable (local .env via load_dotenv,
         or a top-level Streamlit Cloud secret, which is exposed as an env var).
      2. ``st.secrets`` — covers Streamlit Cloud secrets that are NOT surfaced
         as env vars (e.g. nested under a section, or on some runtime versions).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        try:
            import streamlit as st  # optional: only present in the UI runtime

            if "OPENAI_API_KEY" in st.secrets:
                api_key = st.secrets["OPENAI_API_KEY"]
        except Exception:
            api_key = None
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Locally: copy .env.example to .env and "
            "add your key. On Streamlit Cloud: add it under Manage app → "
            "Settings → Secrets as a top-level entry: OPENAI_API_KEY = \"sk-...\"."
        )
    return api_key


def get_openai_client() -> OpenAI:
    """Return a raw OpenAI client (kept for any non-LangChain callers)."""
    return OpenAI(api_key=get_openai_api_key())
