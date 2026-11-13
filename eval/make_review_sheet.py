#!/usr/bin/env python3
"""Write a blind grading sheet for one real run: no suggested grades.

Zero provider calls. For every request in the run's results file it lists the
question, the reply the person received, the passages that were retrieved, and
Aloo's must-include facts and gold excerpts. The grade columns are left empty
for Aloo. Run from the repo root:

    .venv/bin/python -m eval.make_review_sheet results/raw/runs/E5-<stamp>/<file>.csv
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
QUESTION_FILES = (ROOT / "eval" / "questions_aloo.yaml", ROOT / "eval" / "questions.yaml")
FIELDS = ["request_id", "question_id", "answerable", "question", "reply", "status", "level",
          "retrieved_passages", "must_include", "gold_excerpts", "grade_aloo", "note_aloo"]


def questions() -> dict[str, dict]:
    by_text: dict[str, dict] = {}
    for path in QUESTION_FILES:
        if path.exists():
            for item in yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]:
                by_text.setdefault(" ".join(str(item["question"]).split()), item)
    return by_text


def main(run_file: Path) -> None:
    chunks = {}
    for setting in ("R1", "R2", "R3"):
        for line in (ROOT / "library" / "chunks" / f"{setting}.jsonl").read_text(encoding="utf-8").splitlines():
            chunk = json.loads(line)
            chunks[chunk["chunk_id"]] = chunk
    known = questions()
    rows = []
    with run_file.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            trace = json.loads((ROOT / "results" / "traces" / f"{row['request_id']}.json").read_text(encoding="utf-8"))
            item = known.get(" ".join(trace["question"].split()), {})
            passages = [
                f"[{chunks[c]['paper_id']} p.{chunks[c]['page_number']}] " + " ".join(chunks[c]["text"].split())
                for c in trace["retrieved_chunk_ids"] if c in chunks
            ]
            rows.append({
                "request_id": row["request_id"], "question_id": item.get("id", trace.get("question_id") or ""),
                "answerable": item.get("answerable", ""), "question": trace["question"],
                "reply": trace["answer"], "status": row["status"], "level": trace["level"],
                "retrieved_passages": "\n---\n".join(passages),
                "must_include": "\n".join(f"- {fact}" for fact in item.get("must_include", [])),
                "gold_excerpts": "\n".join(
                    f"[{g['paper_id']} p.{g['page_number']}] {g['excerpt']}" for g in item.get("gold_passages", [])
                ) or item.get("gold_answer", ""),
                "grade_aloo": "", "note_aloo": "",
            })
    out = ROOT / "eval" / f"review_sheet_{run_file.parent.name}_{run_file.stem}.csv"
    with out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out.relative_to(ROOT)} ({len(rows)} rows). Fill grade_aloo with: correct, wrong, "
          "refused_correctly or refused_wrongly.")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
