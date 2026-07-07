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
    """Return the OpenAI API key, or raise a clear error if it's missing."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return api_key


def get_openai_client() -> OpenAI:
    """Return a raw OpenAI client (kept for any non-LangChain callers)."""
    return OpenAI(api_key=get_openai_api_key())
