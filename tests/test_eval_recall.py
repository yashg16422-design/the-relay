from __future__ import annotations

import json

import pytest

from eval.recall import EvalQuestion, GoldPassage, _passage_found, load_questions


def test_gold_passage_matches_exact_pdf_page_and_excerpt() -> None:
    gold = GoldPassage("1706.03762v7", 4, "Transformer uses attention mechanisms")
    chunks = [
        {
            "paper_id": "1706.03762v7",
            "page_number": 4,
            "text": "The Transformer uses attention mechanisms.",
        }
    ]

    assert _passage_found(gold, chunks) is True
    assert _passage_found(GoldPassage("1706.03762v7", 5, gold.excerpt), chunks) is False


def test_question_loader_requires_page_cited_gold_for_answerable_questions(tmp_path) -> None:
    path = tmp_path / "questions.yaml"
    path.write_text(
        "questions:\n"
        "  - id: q1\n"
        "    question: What is the Transformer?\n"
        "    type: factual\n"
        "    answerable: true\n"
        "    gold_passages:\n"
        "      - paper_id: 1706.03762v7\n"
        "        page_number: 4\n"
        "        excerpt: Transformer uses attention mechanisms\n",
        encoding="utf-8",
    )

    result = load_questions(path)

    assert result[0].question_id == "q1"
    assert result[0].gold_passages[0].page_number == 4


def test_unanswerable_question_needs_no_gold_passage(tmp_path) -> None:
    path = tmp_path / "questions.yaml"
    path.write_text(
        "questions:\n"
        "  - id: q2\n"
        "    question: What is the secret number?\n"
        "    type: unanswerable\n"
        "    answerable: false\n"
        "    gold_passages: []\n",
        encoding="utf-8",
    )

    result = load_questions(path)
    assert result[0].answerable is False
    assert result[0].gold_passages == ()


def test_answerable_question_without_gold_is_rejected(tmp_path) -> None:
    path = tmp_path / "questions.yaml"
    path.write_text(
        "questions:\n"
        "  - id: q1\n"
        "    question: What?\n"
        "    type: factual\n"
        "    answerable: true\n"
        "    gold_passages: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="needs at least one gold passage"):
        load_questions(path)
