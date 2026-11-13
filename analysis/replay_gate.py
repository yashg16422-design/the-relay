#!/usr/bin/env python3
"""Replay the sentence gate offline on the raw drafts saved in traces (zero provider calls).

For every post-rework trace that stored a draft, run the gate again on that
draft with the passages that were retrieved, sentence by sentence, and print
which sentence failed which check. Use it after changing relay/gate.py to see
the effect on real drafts before spending tokens. Run from the repo root:

    .venv/bin/python analysis/replay_gate.py [request-id-substring]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from relay.gate import CitationGate  # noqa: E402


def main(needle: str = "") -> None:
    chunks = {}
    for setting in ("R1", "R2", "R3"):
        for line in (ROOT / "library" / "chunks" / f"{setting}.jsonl").read_text(encoding="utf-8").splitlines():
            chunk = json.loads(line)
            chunks[chunk["chunk_id"]] = chunk
    gate = CitationGate()
    accepted = total = 0
    for path in sorted((ROOT / "results" / "traces").glob("*.json")):
        trace = json.loads(path.read_text(encoding="utf-8"))
        draft = trace.get("raw_answer")
        if not draft or needle not in trace["request_id"] or "NOT_IN_PAPERS" in draft:
            continue
        retrieved = [chunks[c] for c in trace["retrieved_chunk_ids"] if c in chunks]
        allowed = set((trace.get("decide") or {}).get("papers") or []) or None
        result = gate.check(draft, retrieved_chunks=retrieved, allowed_paper_ids=allowed,
                            question=trace["question"])
        total += 1
        accepted += result.accepted
        if not result.accepted:
            print(f"\n{trace['request_id']}: {result.reason}")
            for sentence in gate._sentences(draft):
                one = gate.check(sentence, retrieved_chunks=retrieved, allowed_paper_ids=allowed,
                                 question=trace["question"])
                if not one.accepted:
                    print(f"   FAILS ({one.reason}): {sentence[:260]}")
    print(f"\n{accepted} of {total} saved drafts pass the gate")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
