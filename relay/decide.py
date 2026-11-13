"""Pure helpers for the "understand" and "decide" steps (decision record 0006).

Nothing here calls a model. The pipeline uses these to read model replies
without turning a cut-off or badly formatted reply into a refusal.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

ABSTAIN_SENTINEL = "NOT_IN_PAPERS"
JudgeState = Literal["yes", "no", "uncertain"]

PAPER_IDS = {
    "transformer": "1706.03762v7",
    "moshi": "2410.00037v1",
    "hnet": "2507.07955v2",
}
# Words in a QUESTION that name a paper. Titles and arXiv ids come from the
# brief; "Helium", "Mimi", "Inner Monologue" (Moshi p. 3) and "H-Net"
# (Dynamic Chunking p. 1) are the names those papers give their own parts.
# The bare word "Transformer" is deliberately absent: Moshi has a "Depth
# Transformer" and H-Net a "Transformer baseline", so the word names no paper.
_QUESTION_KEYWORDS = {
    "transformer": r"attention is all you need|1706\.03762",
    "moshi": r"\bmoshi\b|\bmimi\b|\bhelium\b|inner monologue|2410\.00037",
    "hnet": r"\bh-?nets?\b|dynamic chunking|2507\.07955",
}
_FENCE = re.compile(r"^```[A-Za-z]*\s*|\s*```$")


def first_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in a reply, ignoring code fences and stray prose."""
    cleaned = _FENCE.sub("", text.strip())
    decoder = json.JSONDecoder()
    for position, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def papers_from_question(question: str) -> list[str]:
    """Paper ids the question names outright, in a fixed order."""
    lowered = question.casefold()
    return [
        PAPER_IDS[name]
        for name, pattern in _QUESTION_KEYWORDS.items()
        if re.search(pattern, lowered)
    ]


def parse_understanding(text: str, question: str) -> tuple[str, list[str], str, str]:
    """Read the understand step's reply.

    Returns (search query, paper ids, where the papers came from, parse state).
    Papers named in the question and papers the model names are both kept.
    """
    value = first_json_object(text)
    query = value.get("query") if value else None
    parse_state = "json" if isinstance(query, str) and query.strip() else "fallback"
    query = query.strip() if parse_state == "json" else question

    # Union, not override: a keyword can miss the paper ("Depth Transformer"
    # is Moshi's) and the model can miss it too; either one naming a paper
    # keeps that paper in scope.
    named = papers_from_question(question)
    guessed = value.get("papers") if value else None
    guessed_ids = [
        PAPER_IDS[str(item).casefold()]
        for item in (guessed if isinstance(guessed, list) else [])
        if str(item).casefold() in PAPER_IDS
    ]
    papers = list(dict.fromkeys([*named, *guessed_ids]))
    if named and guessed_ids:
        source = "question_keywords+understand_step"
    elif named:
        source = "question_keywords"
    elif guessed_ids:
        source = "understand_step"
    else:
        source = "none"
    return query, papers, source, parse_state


def parse_judgment(text: str, finish_reason: str | None = None) -> tuple[JudgeState, str]:
    """Three-state reading of the judge's reply.

    Only an explicit ``"answerable": false`` is a "no". A reply that was cut
    off, is not JSON, or has no boolean is "uncertain": the pipeline then lets
    the drafter and the sentence gate decide instead of refusing.
    """
    value = first_json_object(text)
    if value is None:
        cause = "cut off by the token cap" if finish_reason == "length" else "not valid JSON"
        return "uncertain", f"answerability reply was {cause}"
    answerable = value.get("answerable")
    if not isinstance(answerable, bool):
        return "uncertain", "answerability reply had no boolean decision"
    reason = value.get("reason", "")
    return ("yes" if answerable else "no"), reason if isinstance(reason, str) else ""


def is_abstention(answer: str) -> bool:
    """True when the drafter replied with the sentinel instead of a cited answer."""
    normalized = re.sub(r"[\s_-]+", "_", answer.upper())
    return ABSTAIN_SENTINEL in normalized and "[[cite:" not in answer


RESERVED_OPTIONS = frozenset({"model", "messages", "stream"})


def validate_options(name: str, options: dict[str, Any]) -> dict[str, Any]:
    """Reject request options that would crash a call instead of configuring it."""
    clash = RESERVED_OPTIONS.intersection(options)
    if clash:
        raise ValueError(f"{name} cannot set {sorted(clash)}")
    return dict(options)


def pipeline_kwargs_from_config(config: dict[str, Any]) -> dict[str, Any]:
    """Constructor arguments for CitedAnswerPipeline read from config/pipeline.yaml.

    The load generator, the quality runner and the real steady run build their
    pipeline through this, so a run cannot silently use a different decide mode
    or gate from the config. (load/run_retry_worse.py does not: it is the old
    one-request retry demo with a stub retriever and is being superseded.)
    """
    from relay.gate import CitationGate

    return {
        "decide_mode": str(config.get("decide_mode", "llm_judge")),
        "precheck_min_bm25": float(config.get("precheck_min_bm25", 0.0)),
        "scope_to_named_papers": bool(config.get("scope_to_named_papers", True)),
        "judge_retry_options": dict(config.get("judge_retry_options") or {}),
        "options_by_step": dict(config.get("options_by_step") or {}),
        "content_word_query": bool(config.get("content_word_query", False)),
        "options_by_model": dict(config.get("options_by_model") or {}),
        "gate": CitationGate(strict=bool(config.get("gate_strict", True))),
    }
