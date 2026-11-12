from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from relay.trace import RequestTrace, Span, write_trace


def sample_trace(request_id: str = "req-1") -> RequestTrace:
    return RequestTrace(
        request_id=request_id,
        question_id="Q1",
        question="What is the question?",
        answer="A cited answer.",
        retrieved_chunk_ids=["chunk-1"],
        scenario="steady",
        arrival="2026-11-09T10:00:00.000Z",
        deadline="2026-11-09T10:00:05.000Z",
        level="full",
        cache_hit=False,
        coalesced=False,
        spans=[Span(name="retrieve", start_ms=0, end_ms=5, kind="work")],
    )


def test_trace_writes_contract_fields_and_preserves_unknown_tokens(tmp_path) -> None:
    path = write_trace(sample_trace(), tmp_path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["request_id"] == "req-1"
    assert payload["question_id"] == "Q1"
    assert payload["question"] == "What is the question?"
    assert payload["answer"] == "A cited answer."
    assert payload["retrieved_chunk_ids"] == ["chunk-1"]
    assert payload["spans"][0]["kind"] == "work"
    assert payload["spans"][0]["tokens_in"] is None


def test_trace_is_append_only(tmp_path) -> None:
    write_trace(sample_trace(), tmp_path)

    with pytest.raises(FileExistsError, match="append-only"):
        write_trace(sample_trace(), tmp_path)


def test_span_rejects_negative_duration() -> None:
    with pytest.raises(ValidationError, match="cannot precede"):
        Span(name="bad", start_ms=5, end_ms=4, kind="work")
