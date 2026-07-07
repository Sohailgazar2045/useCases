# Use Case 2 — Cash Application (Accounts Receivable)

Automates AR cash application: match an incoming **payment / remittance** to the
right **open invoice(s)** in the mock D365 (SQLite) and post it. Clean exact
matches auto-post; the messy long tail (short-pay, overpay, no-reference,
multi-invoice, exceptions) routes to a human via LangGraph interrupt/resume.

Full rationale and phased plan: [`plan/UC2_EXECUTION_PLAN.md`](../../plan/UC2_EXECUTION_PLAN.md).

## Stack
Python + **OpenAI** (extraction/reasoning) + **LangGraph** (branching, shared
state, human-in-the-loop) + **SQLite** (mock D365). Uses the same
`OPENAI_API_KEY` / `OPENAI_MODEL` as Use Case 1 (via `shared/config.py`).

## Layout (maps to the plan's modules)
```
usecase2/
├─ state.py            # CashAppState TypedDict (§2.5)
├─ config.py           # thresholds, reason codes + OpenAI client (from shared/config.py)
├─ db.py               # Module 1 — mock D365 data-access layer (+ disputes)
├─ graph.py            # Module 6 — LangGraph assembly + edges + checkpointer
├─ service.py          # start()/resume() seam over the graph (SqliteSaver)
├─ run.py              # CLI runner: push one payment through the graph
├─ ui.py               # Module 9 — Streamlit review screen (render() hub entry)
├─ nodes/
│  ├─ ingest.py        # Module 2
│  ├─ extract.py       # Module 3 (OpenAI) — deductions + reason codes
│  ├─ match.py         # Module 4 (tiered matching engine)
│  ├─ decision.py      # Module 5 (confidence + routing + proposed resolution)
│  ├─ human_review.py  # Module 7 (interrupt/resume)
│  └─ post.py          # Module 8 (post + disputes + audit)
├─ data/
│  ├─ seed.py          # synthetic seed hitting every match path
│  └─ sample_payments/ # 9 synthetic remittance docs, one per situation
└─ tests/
   ├─ test_matching.py # matching engine (pure, no DB/LLM/LangGraph)
   └─ test_decision.py # the auto-post safety gate
```

## Quick start
```bash
python -m usecases.usecase2.data.seed        # build + seed mock D365
pytest usecases/usecase2/tests               # 17 tests (matching + safety gate)
python -m usecases.usecase2.run              # run one payment end-to-end (CLI)
streamlit run Home.py                        # open the hub → Cash Application (review UI)
```

## Build order (see plan §4)
- **Phase 1** ✅ data layer (`db.py`, `seed.py`) + state.
- **Phase 2** ✅ happy path — `extract.py`, ingest→extract→match→post auto-posts.
- **Phase 3** ✅ long tail — deduction/reason-code matching (dispute/credit/partial) + routing.
- **Phase 4** ✅ HITL — `interrupt()`/`resume` via `SqliteSaver` + Streamlit approval screen.
- **Phase 5** ⬜ demo polish — more scenarios, styling, run-of-show.

> Phases 1–4 are built and verified. The whole pipeline runs end-to-end: clean
> payments auto-post; the long tail pauses in the Streamlit review screen for
> Approve / Adjust / Reject.
