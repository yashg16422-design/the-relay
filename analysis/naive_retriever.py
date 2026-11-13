"""Show how plain BM25 goes wrong on this library, and measure candidate fixes.

Zero provider calls. "Naive" here means: BM25 over the chunk text, the raw
question as the query, no paper filter. For each failure mechanism the script
prints the real top passage (paper, page, text), then measures on the 35 legacy
questions how often the gold passage is retrieved with each fix switched on.
Writes results/naive_retriever_report.json. Run from the repo root:

    .venv/bin/python analysis/naive_retriever.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.recall import _passage_found, load_questions  # noqa: E402
from relay.decide import papers_from_question  # noqa: E402
from relay.retrieval import PaperRetriever, content_query, is_damaged_table, tokenize  # noqa: E402

CHUNK_DIR = ROOT / "library" / "chunks"
REPORT = ROOT / "results" / "naive_retriever_report.json"
TOP_K = {"R1": 3, "R2": 4, "R3": 6}
EXAMPLES = [
    {
        "mechanism": "1. Cross-paper term collision",
        "question": "How many GPUs was Moshi trained on?",
        "why": "The Moshi paper never states a GPU count; 'trained' and 'GPUs' match the "
               "Transformer paper's hardware section instead.",
        "fix": "paper_scope",
    },
    {
        "mechanism": "2. Appendix chat transcripts hijack question words",
        "question": "What does the Moshi paper say about SoundStream?",
        "why": "Moshi's appendix prints sample dialogues where every other line starts "
               "'MOSHI:' and which are full of 'what', 'say' and 'about'.",
        "fix": "content_query",
    },
    {
        "mechanism": "3. Tables and figures shredded by PDF extraction",
        "question": "What is the frame rate of Mimi?",
        "why": "A table row label was extracted as its own tiny passage with the value "
               "column lost, so it matches the words and carries no answer.",
        "fix": "drop_debris",
    },
    {
        "mechanism": "3b. Same mechanism, figure axis labels",
        "question": "What bits-per-byte does H-Net (2-stage) XL reach?",
        "why": "The top passage is the tick labels of a plot.",
        "fix": "drop_debris",
    },
]


def load_chunks(setting: str) -> list[dict[str, Any]]:
    path = CHUNK_DIR / f"{setting}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def is_debris(chunk: dict[str, Any]) -> bool:
    """A passage that is mostly numbers or labels: plot ticks, orphaned table cells."""
    tokens = tokenize(chunk["text"])
    wordy = [token for token in tokens if re.fullmatch(r"[a-z][a-z-]{2,}", token)]
    return len(set(wordy)) < 12


def describe(hit: Any) -> dict[str, Any]:
    chunk = hit.chunk
    return {
        "paper_id": chunk["paper_id"],
        "page": chunk["page_number"],
        "kind": chunk["kind"],
        "chunk_id": chunk["chunk_id"],
        "bm25": round(hit.bm25_score, 2),
        "text": " ".join(chunk["text"].split())[:220],
    }


def search(retriever: PaperRetriever, question: str, fixes: set[str]) -> Any:
    query = content_query(question) if "content_query" in fixes else question
    papers = papers_from_question(question) if "paper_scope" in fixes else []
    return retriever.search(query, paper_ids=papers or None).hits


def build(setting: str, fixes: set[str]) -> PaperRetriever:
    chunks = load_chunks(setting)
    if "drop_debris" in fixes:
        chunks = [chunk for chunk in chunks if not is_debris(chunk)]
    if "drop_damaged_tables" in fixes:
        chunks = [chunk for chunk in chunks if not is_damaged_table(chunk)]
    return PaperRetriever(chunks, setting=setting, top_k=TOP_K[setting])


VARIANTS: list[tuple[str, set[str]]] = [
    ("naive BM25", set()),
    ("+ paper scope", {"paper_scope"}),
    ("+ content-word query", {"paper_scope", "content_query"}),
    ("  ...and drop damaged tables", {"paper_scope", "content_query", "drop_damaged_tables"}),
    ("  ...and drop debris passages", {"paper_scope", "content_query", "drop_debris"}),
]
# What the pipeline would adopt: the two fixes that raised recall.
ADOPTED = {"paper_scope", "content_query"}


def main() -> None:
    report: dict[str, Any] = {"examples": [], "recall": [], "corpus": {}}
    for setting in TOP_K:
        chunks = load_chunks(setting)
        tables = [chunk for chunk in chunks if chunk.get("kind") == "table"]
        report["corpus"][setting] = {
            "chunks": len(chunks),
            "debris_passages": sum(is_debris(chunk) for chunk in chunks),
            "table_chunks": len(tables),
            "tables_with_detached_decimals": sum(is_damaged_table(chunk) for chunk in tables),
        }
    print("corpus:", json.dumps(report["corpus"]))

    for example in EXAMPLES:
        print(f"\n== {example['mechanism']}\n   Q: {example['question']}")
        entry = {**example, "by_setting": {}}
        for setting in TOP_K:
            naive = describe(search(build(setting, set()), example["question"], set())[0])
            own_fix = ADOPTED | {example["fix"]}
            fixed = describe(search(build(setting, own_fix), example["question"], own_fix)[0])
            entry["by_setting"][setting] = {"naive_top": naive, "fixed_top": fixed}
            print(f"   {setting} naive top: {naive['paper_id']} p.{naive['page']} "
                  f"[{naive['kind']}] {naive['text'][:110]!r}")
            print(f"   {setting} fixed top: {fixed['paper_id']} p.{fixed['page']} "
                  f"[{fixed['kind']}] {fixed['text'][:110]!r}")
        report["examples"].append(entry)

    questions = [q for q in load_questions() if q.answerable]
    print(f"\n== gold passage retrieved, {len(questions)} answerable legacy questions "
          "(eval/questions.yaml, assistant-proposed), BM25 only, no re-rank")
    for name, fixes in VARIANTS:
        row: dict[str, Any] = {"variant": name}
        for setting in TOP_K:
            retriever = build(setting, fixes)
            hit_count = 0
            for question in questions:
                hits = [hit.chunk for hit in search(retriever, question.question, fixes)]
                hit_count += all(_passage_found(gold, hits) for gold in question.gold_passages)
            row[setting] = {"questions_with_all_gold": hit_count, "n": len(questions),
                            "pct": round(100 * hit_count / len(questions), 1)}
        report["recall"].append(row)
        print(f"   {name:26s} " + "  ".join(
            f"{s}: {row[s]['questions_with_all_gold']}/{row[s]['n']} ({row[s]['pct']}%)"
            for s in TOP_K))

    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
