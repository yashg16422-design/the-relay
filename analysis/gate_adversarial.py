"""Measure the sentence gate's false-accept and false-reject rates on written attack cases.

Zero model calls. Reads eval/gate_adversarial_cases.yaml and the real R1 chunks,
runs the legacy gate (strict=False) and the strict gate on every case, and writes
results/gate_adversarial_report.json. Run from the repo root:

    .venv/bin/python analysis/gate_adversarial.py
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from relay.gate import CitationGate  # noqa: E402

CASES = ROOT / "eval" / "gate_adversarial_cases.yaml"
CHUNKS = ROOT / "library" / "chunks" / "R1.jsonl"
REPORT = ROOT / "results" / "gate_adversarial_report.json"
ALIAS = re.compile(r"\[\[([A-Z0-9]+)\]\]")


def load_cases() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    spec = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    by_id = {
        row["chunk_id"]: row
        for row in map(json.loads, CHUNKS.read_text(encoding="utf-8").splitlines())
    }
    chunks = {alias: by_id[chunk_id] for alias, chunk_id in spec["chunks"].items()}
    cases = []
    for case in spec["cases"]:
        answer = ALIAS.sub(lambda m: f"[[cite:{spec['chunks'][m.group(1)]}]]", case["answer"])
        cases.append(
            {
                **case,
                "answer": answer,
                "allowed_paper_ids": {spec["papers"][key] for key in case["allowed"]},
            }
        )
    return cases, chunks


def evaluate() -> dict[str, Any]:
    cases, chunks = load_cases()
    retrieved = list(chunks.values())
    gates = {"legacy": CitationGate(strict=False), "strict": CitationGate(strict=True)}
    rows = []
    for case in cases:
        row = {"id": case["id"], "category": case["category"], "expect": case["expect"]}
        for name, gate in gates.items():
            result = gate.check(
                case["answer"],
                retrieved_chunks=retrieved,
                allowed_paper_ids=case["allowed_paper_ids"] or None,
                **({"question": case["question"]} if name == "strict" and case.get("question") else {}),
            )
            row[name] = "accept" if result.accepted else "reject"
            row[f"{name}_reason"] = result.reason
        rows.append(row)

    summary: dict[str, Any] = {}
    for name in gates:
        attacks = [row for row in rows if row["expect"] == "reject"]
        faithful = [row for row in rows if row["expect"] == "accept"]
        by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for row in attacks:
            by_category[row["category"]][0] += row[name] == "accept"
            by_category[row["category"]][1] += 1
        false_accepts = sum(row[name] == "accept" for row in attacks)
        false_rejects = sum(row[name] == "reject" for row in faithful)
        summary[name] = {
            "attacks": len(attacks),
            "false_accepts": false_accepts,
            "false_accept_pct": round(100 * false_accepts / len(attacks), 1),
            "faithful": len(faithful),
            "false_rejects": false_rejects,
            "false_reject_pct": round(100 * false_rejects / len(faithful), 1),
            "false_accepts_by_category": {
                key: {"accepted": hit, "n": n} for key, (hit, n) in sorted(by_category.items())
            },
        }
    return {
        "cases_file": str(CASES.relative_to(ROOT)),
        "case_author": "assistant (Claude Code); same author as the strict gate",
        "summary": summary,
        "rows": rows,
    }


def main() -> None:
    report = evaluate()
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for name, stats in report["summary"].items():
        print(
            f"{name}: false accepts {stats['false_accepts']}/{stats['attacks']} "
            f"({stats['false_accept_pct']}%), false rejects "
            f"{stats['false_rejects']}/{stats['faithful']} ({stats['false_reject_pct']}%)"
        )
        for category, counts in stats["false_accepts_by_category"].items():
            print(f"    {category}: {counts['accepted']}/{counts['n']} accepted")
    print("\nstrict gate misses:")
    for row in report["rows"]:
        if row["expect"] != row["strict"]:
            print(f"    {row['id']} ({row['category']}): {row['strict']} - {row['strict_reason']}")
    print(f"\nwrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
