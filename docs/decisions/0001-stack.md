# 0001 — Python service and retrieval stack

- Status: proposed
- Date: 2026-11-09
- Phase / gate: A

## Problem
The brief requires a working multi-step paper-answering pipeline, at least one tool call and a branch or loop, streaming behavior, retrieval, a measurable stand-in, and reproducible load experiments. The stack must support those needs without making the project impossible to explain or run.

## Options
1. Use one small Python program and the standard library, adding a dependency only when a measured need appears.
2. Use the proposed Python stack: Python 3.11+, asyncio, httpx, FastAPI/uvicorn, Pydantic, PyYAML, pandas/NumPy/matplotlib, PyMuPDF, BM25 and sentence-transformers, with optional reranking.
3. Use another brief-permitted language (C++ or Go) and select equivalent libraries.

## Evidence
No runtime or retrieval measurements have been collected yet. The assignment requires a small implementation and experiments; the proposed stack is a design hypothesis. The bootstrap attempt in the current Codex macOS environment (Python 3.14) stopped because the environment could not resolve/connect to PyPI. This is an unresolved setup failure, not evidence that the dependencies are incompatible. On Aloo's planned Linux/WSL machine with Python 3.11+, verify that the complete dependency install succeeds in `.venv` and record the Python and package versions. After that, exercise streaming and stand-in requests, and compare retrieval configurations on the labeled questions.

## Decision (builder's recommendation)
Recommend option 2 so the project can implement the intended streaming client, stand-in HTTP service, PDF ingestion, retrieval variants, and analysis workflow in one ecosystem. Keep reranking optional and use measured retrieval quality, latency, and token use to decide whether it earns its cost. This recommendation is provisional until Aloo accepts it and the environment is verified.

## What it costs
More packages and setup time than option 1; sentence-transformers and a reranker can add CPU time, downloads, and operational complexity. Python and the selected libraries may have compatibility or performance limits on the available machine. Option 3 could offer different runtime characteristics but would require a different toolchain and equivalent integrations.

## What would change our mind
Change the stack or defer a dependency if it cannot be installed on the chosen machine, fails the streaming/stand-in requirements, or its measured retrieval quality does not justify its latency and resource cost.

## CTO review
`GO WITH CHANGES` — specify the machine/runtime and success check; record the PyPI failure as unresolved evidence. No streaming, stand-in, or retrieval measurements exist yet. Do not make a real API call without Aloo's explicit approval. Acceptance does not mean the environment or stack is verified.

## Aloo's call
Accepted by Aloo on 2026-11-09: “ok accepted bro also the goal should be to maximise the score and technicality no matter even if it seems out of scope as long as its helping okay so yeah begin now...” The stack choice is accepted as a direction; environment verification remains outstanding.
