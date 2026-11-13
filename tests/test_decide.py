"""Decide-step parsing and the pipeline branches it drives (decision record 0006)."""

from __future__ import annotations

import asyncio
import json

from relay.decide import (
    is_abstention,
    papers_from_question,
    parse_judgment,
    parse_understanding,
)
from relay.llm_client import LLMResponse, StreamCapture, TokenUsage
from relay.pipeline import SAFE_REFUSAL, CitedAnswerPipeline
from relay.retrieval import PaperRetriever

T, M = "1706.03762v7", "2410.00037v1"
T_ID, M_ID = "0123456789abcdef", "fedcba9876543210"
CHUNKS = [
    {"chunk_id": T_ID, "paper_id": T, "paper_title": "Attention Is All You Need",
     "page_number": 7, "section": "5.2", "text": "We trained our models on 8 NVIDIA P100 GPUs."},
    {"chunk_id": M_ID, "paper_id": M, "paper_title": "Moshi", "page_number": 1,
     "section": "Abstract", "text": "Moshi has a theoretical latency of 160ms."},
]


def test_truncated_or_malformed_judge_reply_is_uncertain_not_no() -> None:
    assert parse_judgment('{"answerable": tr', "length")[0] == "uncertain"
    assert "token cap" in parse_judgment('{"answerable": tr', "length")[1]
    assert parse_judgment("I think yes")[0] == "uncertain"
    assert parse_judgment('{"reason": "x"}')[0] == "uncertain"


def test_judge_reply_in_code_fence_or_prose_is_read() -> None:
    assert parse_judgment('```json\n{"answerable": true, "reason": "ok"}\n```') == ("yes", "ok")
    assert parse_judgment('Sure: {"answerable": false, "reason": "absent"}')[0] == "no"


def test_papers_named_by_question_and_by_model_are_both_kept() -> None:
    query, papers, source, state = parse_understanding(
        '{"query": "gpu count", "papers": ["moshi"]}',
        "How many GPUs was Moshi trained on?",
    )
    assert (query, papers, state) == ("gpu count", [M], "json")
    assert source == "question_keywords+understand_step"


def test_bare_word_transformer_does_not_scope_a_moshi_or_hnet_question() -> None:
    # CTO review 2026-11-12: these were all scoped to the Transformer paper.
    for question, guess, expected in [
        ("How many parameters does the Depth Transformer have?", "moshi", [M]),
        ("What does the Temporal Transformer model?", "moshi", [M]),
        ("How does H-Net compare with the isotropic Transformer baseline?", "hnet",
         ["2507.07955v2"]),
    ]:
        _, papers, _, _ = parse_understanding(
            json.dumps({"query": "q", "papers": [guess]}), question
        )
        assert papers == expected, question


def test_models_paper_guess_is_used_when_question_names_none() -> None:
    _, papers, source, _ = parse_understanding(
        '{"query": "bleu", "papers": ["transformer", "bogus"]}', "What BLEU score was reported?"
    )
    assert (papers, source) == ([T], "understand_step")
    query, papers, source, state = parse_understanding("not json", "What BLEU score was reported?")
    assert (query, papers, source, state) == ("What BLEU score was reported?", [], "none", "fallback")


def test_question_keywords_cover_all_three_papers() -> None:
    assert papers_from_question("Compare H-Net with Attention Is All You Need") == [T, "2507.07955v2"]
    assert papers_from_question("How many layers does the Transformer encoder have?") == []
    assert papers_from_question("What is Mimi's frame rate?") == [M]
    assert papers_from_question("What optimizer was used?") == []


def test_abstention_requires_sentinel_and_no_citation() -> None:
    assert is_abstention("NOT_IN_PAPERS")
    assert is_abstention(" not_in_papers. ")
    assert is_abstention("Not in papers")
    assert not is_abstention(f"NOT_IN_PAPERS is wrong. [[cite:{T_ID}]]")


class Client:
    """Scripted model: one understand reply, a list of judge replies, a list of drafts."""

    def __init__(self, *, judge=None, drafts=None, papers=None):
        self.judge = list(judge or [])
        self.drafts = list(drafts or [])
        self.papers = papers or []
        self.judge_options: list[dict] = []
        self.draft_calls = 0

    async def complete(self, messages, **kwargs):
        if "Rewrite the user's question" in messages[0]["content"]:
            text, finish = json.dumps({"query": "GPUs trained", "papers": self.papers}), "stop"
        else:
            self.judge_options.append(kwargs)
            text, finish = self.judge.pop(0)
        return LLMResponse(text=text, model="stub", status_code=200, headers={},
                           usage=TokenUsage(10, 4, 0, 0), time_to_first_chunk_ms=None,
                           total_ms=1, finish_reason=finish)

    async def stream(self, messages, *, capture: StreamCapture, **kwargs):
        self.draft_calls += 1
        answer = self.drafts.pop(0)
        yield answer
        capture.response = LLMResponse(text=answer, model="stub", status_code=200, headers={},
                                       usage=TokenUsage(20, 9, 0, 0),
                                       time_to_first_chunk_ms=0.2, total_ms=1,
                                       finish_reason="stop")


def run(client, question="How many GPUs were the models in Attention Is All You Need trained on?", **kwargs):
    pipeline = CitedAnswerPipeline(client, PaperRetriever(CHUNKS, setting="R1", top_k=2), **kwargs)

    async def go():
        loop = asyncio.get_running_loop()
        return await pipeline.run(question, request_id="r1", experiment="test",
                                  deadline_monotonic=loop.time() + 5)

    return asyncio.run(go())


GOOD = f"The models were trained on 8 NVIDIA P100 GPUs. [[cite:{T_ID}]]"


def test_truncated_judge_gets_one_retry_then_proceeds_to_draft() -> None:
    client = Client(judge=[('{"answerable": t', "length"), ("still not json", "stop")], drafts=[GOOD],)
    result = run(client, judge_retry_options={"response_format": {"type": "json_object"}})
    assert result.answerable is True and "PDF p. 7" in result.answer
    assert len(client.judge_options) == 2
    assert "response_format" not in client.judge_options[0]
    assert client.judge_options[1]["response_format"] == {"type": "json_object"}
    judge_spans = [s for s in result.spans if s.name.startswith("decide_answerability")]
    assert [s.parse_state for s in judge_spans] == ["uncertain", "uncertain"]
    assert judge_spans[0].finish_reason == "length"
    assert judge_spans[0].raw_text == '{"answerable": t'
    assert result.decide["judge_state"] == "uncertain" and result.decide["outcome"] == "answered"


def test_explicit_no_from_judge_refuses_without_drafting() -> None:
    client = Client(judge=[('{"answerable": false, "reason": "absent"}', "stop")])
    result = run(client)
    assert result.answer == SAFE_REFUSAL and client.draft_calls == 0
    assert result.decide["outcome"] == "refused_by_judge"


def test_precheck_mode_makes_no_judge_call_and_drafter_can_abstain() -> None:
    client = Client(drafts=["NOT_IN_PAPERS", GOOD])
    result = run(client, decide_mode="precheck_abstain")
    assert result.answer == SAFE_REFUSAL and result.answerable is False
    assert client.judge_options == [] and client.draft_calls == 1  # no repair after abstaining
    assert result.decide["outcome"] == "drafter_abstained"
    assert any(s.name == "decide_precheck" and s.model is None for s in result.spans)


def test_precheck_refuses_with_zero_model_calls_after_understand() -> None:
    client = Client(drafts=[GOOD])
    result = run(client, decide_mode="precheck_abstain", precheck_min_bm25=1e9)
    assert result.answer == SAFE_REFUSAL and client.draft_calls == 0
    assert result.decide["precheck"] == "fail"
    assert result.decide["outcome"] == "refused_by_precheck"


def test_retrieval_and_gate_are_scoped_to_the_paper_the_question_names() -> None:
    # A Moshi question: the Transformer GPU passage must not be retrieved,
    # and a draft that cites it anyway must not pass the gate.
    client = Client(drafts=[GOOD], judge=[('{"answerable": true}', "stop")])
    result = run(client, question="How many GPUs was Moshi trained on?", max_repairs=0)
    assert result.retrieved_chunk_ids == (M_ID,)
    assert result.answer == SAFE_REFUSAL
    assert result.decide["papers"] == [M] and result.decide["paper_source"] == "question_keywords"


def test_failed_judge_retry_falls_through_to_draft_instead_of_killing_the_request() -> None:
    from relay.llm_client import LLMRequestError

    class RetryRejected(Client):
        async def complete(self, messages, **kwargs):
            if "response_format" in kwargs:
                raise LLMRequestError("bad option", status_code=400, headers={}, body="")
            return await super().complete(messages, **kwargs)

    client = RetryRejected(judge=[("cut off", "length")], drafts=[GOOD])
    result = run(client, judge_retry_options={"response_format": {"type": "json_object"}})
    assert result.status == "ok" and result.answerable is True
    assert result.raw_answer == GOOD


def test_step_options_may_raise_the_cap_but_not_replace_the_model() -> None:
    import pytest

    client = Client(judge=[("cut", "length"), ('{"answerable": true}', "stop")], drafts=[GOOD])
    run(client, judge_retry_options={"max_completion_tokens": 512})
    assert client.judge_options[1]["max_completion_tokens"] == 512
    with pytest.raises(ValueError):
        run(Client(), options_by_step={"draft": {"model": "other"}})


def test_runners_read_decide_settings_from_the_same_config_helper() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for runner in ("eval/run_quality.py", "load/run_real_steady.py", "load/loadgen.py"):
        assert "**pipeline_kwargs_from_config(pipeline_config)" in (root / runner).read_text()
