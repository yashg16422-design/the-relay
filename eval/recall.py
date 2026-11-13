#!/usr/bin/env python3
"""Measure retrieval recall@k against human-labeled paper/page/excerpt evidence."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from library.ingest import CHUNKS_DIR, SETTINGS
from relay.retrieval import PaperRetriever


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = PROJECT_ROOT / "eval" / "questions.yaml"


@dataclass(frozen=True)
class GoldPassage:
    paper_id: str
    page_number: int
    excerpt: str


@dataclass(frozen=True)
class EvalQuestion:
    question_id: str
    question: str
    question_type: str
    answerable: bool
    gold_passages: tuple[GoldPassage, ...]


@dataclass(frozen=True)
class RecallRow:
    question_id: str
    setting: str
    k: int
    found: int
    relevant: int
    recall: float | None


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", text.casefold()).split())


def load_questions(path: str | Path = QUESTIONS_PATH) -> list[EvalQuestion]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or not isinstance(raw.get("questions"), list):
        raise ValueError("questions.yaml must contain a questions list")
    questions: list[EvalQuestion] = []
    seen: set[str] = set()
    for item in raw["questions"]:
        if not isinstance(item, Mapping):
            raise ValueError("each question entry must be an object")
        question_id = str(item.get("id", "")).strip()
        question = str(item.get("question", "")).strip()
        question_type = str(item.get("type", "")).strip()
        answerable = item.get("answerable")
        if not question_id or question_id in seen:
            raise ValueError("question IDs must be non-empty and unique")
        if not question or not isinstance(answerable, bool):  # "type" is optional
            raise ValueError(f"question {question_id} is missing required fields")
        seen.add(question_id)
        gold_raw = item.get("gold_passages", [])
        if not isinstance(gold_raw, list):
            raise ValueError(f"question {question_id} gold_passages must be a list")
        passages = []
        for passage in gold_raw:
            if not isinstance(passage, Mapping):
                raise ValueError(f"question {question_id} contains an invalid gold passage")
            paper_id = str(passage.get("paper_id", "")).strip()
            page = passage.get("page_number")
            excerpt = str(passage.get("excerpt", "")).strip()
            if not paper_id or isinstance(page, bool) or not isinstance(page, int) or page < 1 or not excerpt:
                raise ValueError(f"question {question_id} gold passage needs paper_id, page_number, and excerpt")
            passages.append(GoldPassage(paper_id, page, excerpt))
        if answerable and not passages:
            raise ValueError(f"answerable question {question_id} needs at least one gold passage")
        questions.append(
            EvalQuestion(question_id, question, question_type, answerable, tuple(passages))
        )
    return questions


def _passage_found(gold: GoldPassage, chunks: Sequence[Mapping[str, Any]]) -> bool:
    excerpt = _normalize(gold.excerpt)
    return any(
        chunk.get("paper_id") == gold.paper_id
        and chunk.get("page_number") == gold.page_number
        and excerpt in _normalize(str(chunk.get("text", "")))
        for chunk in chunks
    )


def evaluate_recall(
    questions: Sequence[EvalQuestion], *, chunk_dir: str | Path = CHUNKS_DIR
) -> list[RecallRow]:
    retrieval_config_path = PROJECT_ROOT / "config" / "retrieval.yaml"
    retrieval_config = yaml.safe_load(retrieval_config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(retrieval_config, Mapping):
        raise ValueError("retrieval.yaml must contain a YAML mapping")
    reranker_model = retrieval_config.get("reranker_model")
    rows: list[RecallRow] = []
    for setting in SETTINGS:
        retriever = PaperRetriever.from_jsonl(
            setting.name,
            top_k=setting.top_k,
            chunk_dir=chunk_dir,
            reranker_model_name=(str(reranker_model) if setting.rerank and reranker_model else None),
        )
        for question in questions:
            if not question.answerable:
                rows.append(
                    RecallRow(question.question_id, setting.name, setting.top_k, 0, 0, None)
                )
                continue
            result = retriever.search(
                question.question,
                top_k=setting.top_k,
                rerank=setting.rerank,
            )
            found = sum(
                _passage_found(gold, [hit.chunk for hit in result.hits])
                for gold in question.gold_passages
            )
            total = len(question.gold_passages)
            rows.append(
                RecallRow(
                    question.question_id,
                    setting.name,
                    setting.top_k,
                    found,
                    total,
                    found / total if total else None,
                )
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_PATH)
    parser.add_argument("--chunks", type=Path, default=CHUNKS_DIR)
    args = parser.parse_args()
    questions = load_questions(args.questions)
    rows = evaluate_recall(questions, chunk_dir=args.chunks)
    print("question_id,setting,k,found,relevant,recall")
    for row in rows:
        recall = "not measured" if row.recall is None else f"{row.recall:.3f}"
        print(
            f"{row.question_id},{row.setting},{row.k},{row.found},"
            f"{row.relevant},{recall}"
        )


if __name__ == "__main__":
    main()
