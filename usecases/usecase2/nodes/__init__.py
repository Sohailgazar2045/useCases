"""LangGraph nodes for UC2 (Cash Application).

Each module is one node in the graph and takes/returns a partial
``CashAppState``:  ingest → extract → match → decision → human_review → post.
"""
