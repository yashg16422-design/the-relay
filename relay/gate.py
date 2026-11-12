"""Sentence-level citation gate for retrieved paper passages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


CITATION = re.compile(r"\[\[cite:([0-9a-f]{16})\]\]")
NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?(?![A-Za-z])")
QUOTED = re.compile(r"[\"“](.+?)[\"”]")
PROPER_TERM = re.compile(r"\b[A-Z][A-Za-z0-9-]{2,}\b")
SENTENCE_SPLIT = re.compile(
    # Sentence boundaries are punctuation-based, not capitalization-based:
    # lowercase follow-up claims must still be checked independently. Keep a
    # citation marker attached when it follows punctuation at sentence end.
    r"(?<=\]\])\s+(?=\S)|(?<=[.!?])\s+(?!\[\[cite:[0-9a-f]{16}\]\])"
)


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    reason: str
    rendered_answer: str | None
    cited_chunk_ids: tuple[str, ...]


class CitationGate:
    """Reject uncited claims, citations outside retrieved context, and unsupported numbers."""

    def check(
        self,
        answer: str,
        *,
        retrieved_chunks: Sequence[Mapping[str, Any]],
    ) -> GateResult:
        chunks_by_id = {str(chunk.get("chunk_id", "")): chunk for chunk in retrieved_chunks}
        if not answer.strip():
            return GateResult(False, "empty answer", None, ())

        cited: list[str] = []
        for sentence in self._sentences(answer):
            markers = CITATION.findall(sentence)
            if not markers:
                return GateResult(False, "a sentence has no citation marker", None, tuple(cited))
            cited.extend(markers)
            support = []
            for chunk_id in markers:
                chunk = chunks_by_id.get(chunk_id)
                if chunk is None:
                    return GateResult(
                        False,
                        f"citation {chunk_id} was not in retrieved passages",
                        None,
                        tuple(dict.fromkeys(cited)),
                    )
                if not isinstance(chunk.get("page_number"), int):
                    return GateResult(False, f"citation {chunk_id} has no page number", None, tuple(cited))
                if not chunk.get("paper_title") or not chunk.get("paper_id"):
                    return GateResult(False, f"citation {chunk_id} has incomplete paper metadata", None, tuple(cited))
                support.append(str(chunk.get("text", "")).casefold())

            clean_sentence = CITATION.sub("", sentence)
            if not self._numbers_are_supported(clean_sentence, support):
                return GateResult(
                    False,
                    "a number in the sentence is absent from its cited passage",
                    None,
                    tuple(dict.fromkeys(cited)),
                )
            if not self._terms_are_supported(clean_sentence, support):
                return GateResult(
                    False,
                    "a quoted phrase or named term is absent from its cited passage",
                    None,
                    tuple(dict.fromkeys(cited)),
                )

        rendered = CITATION.sub(lambda match: self._render_citation(match.group(1), chunks_by_id), answer)
        return GateResult(True, "all sentence citations passed", rendered, tuple(dict.fromkeys(cited)))

    @staticmethod
    def _sentences(answer: str) -> list[str]:
        return [part.strip() for part in SENTENCE_SPLIT.split(answer.strip()) if part.strip()]

    @staticmethod
    def _numbers_are_supported(sentence: str, sources: Sequence[str]) -> bool:
        numbers = NUMBER.findall(sentence)
        source_numbers = {
            number.casefold()
            for source in sources
            for number in NUMBER.findall(source)
        }
        return all(number.casefold() in source_numbers for number in numbers)

    @staticmethod
    def _terms_are_supported(sentence: str, sources: Sequence[str]) -> bool:
        combined = " ".join(" ".join(source.casefold().split()) for source in sources)
        quoted = QUOTED.findall(sentence)
        if any(
            not CitationGate._whole_phrase_supported(phrase, combined)
            for phrase in quoted
        ):
            return False
        proper_terms = PROPER_TERM.findall(sentence)
        return all(CitationGate._whole_phrase_supported(term, combined) for term in proper_terms)

    @staticmethod
    def _whole_phrase_supported(phrase: str, normalized_sources: str) -> bool:
        normalized_phrase = " ".join(phrase.casefold().split())
        if not normalized_phrase:
            return True
        return re.search(
            rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)",
            normalized_sources,
            flags=re.UNICODE,
        ) is not None

    @staticmethod
    def _render_citation(
        chunk_id: str, chunks_by_id: Mapping[str, Mapping[str, Any]]
    ) -> str:
        chunk = chunks_by_id[chunk_id]
        title = str(chunk["paper_title"])
        versioned_id = str(chunk["paper_id"])
        page = int(chunk["page_number"])
        section = str(chunk.get("section", "")).strip()
        section_text = f", §{section}" if section and section != "Front matter" else ""
        return f"[{title}, arXiv:{versioned_id}, PDF p. {page}{section_text}]"
