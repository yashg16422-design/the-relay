"""Sentence-level citation gate for retrieved paper passages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence


CITATION = re.compile(r"\[\[cite:([0-9a-f]{16})\]\]")
# Models sometimes write the marker in another bracket style: 【cite:id】, [[id]],
# [cite:id], (cite:id). The id is still checked against the retrieved passages, so
# rewriting the brackets changes nothing about what is being claimed or cited.
LOOSE_CITATION = re.compile(
    r"(?:【\s*(?:cite:)?\s*|\[\[\s*(?:cite:)?\s*|\[\s*cite:\s*|\(\s*cite:\s*)([0-9a-f]{16})\s*(?:】|\]\]|\]|\))"
)


def normalize_citations(answer: str) -> str:
    """Rewrite bracket variants of a citation marker to the canonical [[cite:id]]."""
    return LOOSE_CITATION.sub(lambda match: f"[[cite:{match.group(1)}]]", answer)


NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?(?![A-Za-z])")
# Strict mode also reads numbers that carry a unit suffix ("160ms", "7B",
# "12.5Hz"), which NUMBER skips, and treats "100,000" as one number.
NUMBER_WITH_UNIT = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?%?")
THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# A number with what follows it: an attached suffix ("160ms", "7B") and the
# next few words, where a unit would be ("100000 steps", "8 NVIDIA P100 GPUs").
NUMBER_AND_TAIL = re.compile(
    r"(?<![A-Za-z\d.])(\d+(?:\.\d+)?%?)([A-Za-z]{1,3})?((?:[\s\-]+[A-Za-z][\w]*){0,3})"
)
# Words that say what a number counts. If the claim puts one of these after a
# number, the passage must put the same one after that number.
UNIT_WORDS = frozenset(
    """step steps hour hours day days week weeks second seconds minute minutes
    millisecond milliseconds ms token tokens byte bytes layer layers head heads
    parameter parameters epoch epochs hz khz bleu gpu gpus kbps bit bits stage stages
    million billion trillion thousand quantizer quantizers dimension dimensions""".split()
)
# A sentence that only says the passages lack something. It makes no claim about
# the papers' content, so it needs no citation - but it may not carry a number.
DISCLAIMER = re.compile(
    r"\b(?:passages?|papers?|evidence|sources?|excerpts?)\b[^.]*\b(?:do(?:es)? not|don['’]t|doesn['’]t)\b"
    r"[^.]*\b(?:state|say|mention|provide|contain|specify|report|include|give|address)\b",
    re.IGNORECASE,
)
QUOTED = re.compile(r"[\"“](.+?)[\"”]")
PROPER_TERM = re.compile(r"\b[A-Z][A-Za-z0-9-]{2,}\b")
# Explicit negation in a drafted claim. Kept narrow on purpose: these are the
# words that flip the meaning of a sentence.
CLAIM_NEGATION = re.compile(
    r"\b(?:not|no|never|none|neither|nor|without|cannot|fails?|failed|avoids?|avoided|"
    r"unable|lacks?)\b|n['’]t\b",
    re.IGNORECASE,
)
# What counts as negation on the SOURCE side is wider, because papers often
# negate implicitly ("dispensing with recurrence", "instead of").
SOURCE_NEGATION = re.compile(
    r"\b(?:not|no|never|none|neither|nor|without|cannot|instead of|rather than|"
    r"dispens\w+|eschew\w+|absence|lack\w*|unlike|free of|fails? to|unable)\b|n['’]t\b",
    re.IGNORECASE,
)
SOURCE_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n\s*[•\-]\s+")
LINE_BREAK_HYPHEN = re.compile(r"-\s*\n\s*")
# The name each paper gives its own model. A cited passage often says "our
# model" where the answer names it, so strict mode accepts the name when the
# citation is from that paper. Read from each paper's abstract (p. 1).
PAPER_SUBJECT_TERMS: Mapping[str, frozenset[str]] = {
    "1706.03762v7": frozenset({"transformer"}),
    "2410.00037v1": frozenset({"moshi"}),
    "2507.07955v2": frozenset({"h-net", "h-nets"}),
}
WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")
_CONTEXT_STOPWORDS = frozenset(
    """about above after also among another around based because been before being below
    between both does during each every from have having however into itself more most
    much only other over paper papers same should some such than that their them then
    there these they this those through under used uses using very were what when where
    which while will with would model models state states stated reports reported""".split()
)
# Ordinary words that are capitalised only because they start a sentence.
# PROPER_TERM would otherwise demand that "The" appear in the cited passage.
_CAPITALIZED_FUNCTION_WORDS = frozenset(
    """the this that these those their its our they and but for with when where while
    which what who how why each all both one two after before during from into over
    under about there here also however thus then not yes""".split()
)
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
    """Reject uncited claims, citations outside retrieved context, and unsupported numbers.

    ``strict=True`` (the default) adds three checks on top of the original
    lexical gate: paper scope, negation polarity, and number context. The
    checks are still lexical; ``analysis/gate_adversarial.py`` measures what
    they miss. ``strict=False`` reproduces the gate used for every run up to
    2026-11-11 so the two can be compared on the same cases.
    """

    def __init__(self, *, strict: bool = True, context_window_chars: int = 160) -> None:
        if context_window_chars < 1:
            raise ValueError("context_window_chars must be positive")
        self.strict = strict
        self.context_window_chars = context_window_chars

    def check(
        self,
        answer: str,
        *,
        retrieved_chunks: Sequence[Mapping[str, Any]],
        allowed_paper_ids: Collection[str] | None = None,
        question: str | None = None,
    ) -> GateResult:
        chunks_by_id = {str(chunk.get("chunk_id", "")): chunk for chunk in retrieved_chunks}
        if self.strict:
            answer = normalize_citations(answer)
        if not answer.strip():
            return GateResult(False, "empty answer", None, ())

        cited: list[str] = []
        for sentence in self._sentences(answer):
            markers = CITATION.findall(sentence)
            if (
                self.strict and not markers
                and DISCLAIMER.search(sentence)
                # It may not smuggle in a claim ("..., but the rate is high") ...
                and not re.search(r"[;:]|\b(?:but|however|although|though|while|yet|and it|which)\b",
                                  sentence, re.IGNORECASE)
                # ... and may repeat a whole figure from the question, never introduce one.
                and set(re.findall(r"\d+(?:\.\d+)?", sentence))
                <= set(re.findall(r"\d+(?:\.\d+)?", question or ""))
            ):
                continue  # "The passages do not state X." is honest and claims nothing
            if not markers:
                return GateResult(False, "a sentence has no citation marker", None, tuple(cited))
            cited.extend(markers)
            support = []
            # Legacy mode exempts nothing, so it reproduces the pre-2026-11-12 gate exactly.
            subject_terms: set[str] = set(_CAPITALIZED_FUNCTION_WORDS) if self.strict else set()
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
                if (
                    self.strict
                    and allowed_paper_ids
                    and str(chunk.get("paper_id")) not in allowed_paper_ids
                ):
                    return GateResult(
                        False,
                        f"citation {chunk_id} is from a paper the question is not about",
                        None,
                        tuple(dict.fromkeys(cited)),
                    )
                support.extend(self._source_variants(str(chunk.get("text", ""))))
                if self.strict:
                    subject_terms |= PAPER_SUBJECT_TERMS.get(str(chunk.get("paper_id")), frozenset())

            clean_sentence = self._normalize(CITATION.sub("", sentence))
            if not self._numbers_are_supported(clean_sentence, support):
                return GateResult(
                    False,
                    "a number in the sentence is absent from its cited passage",
                    None,
                    tuple(dict.fromkeys(cited)),
                )
            first_word = re.match(r"\W*([A-Za-z][A-Za-z-]*)", clean_sentence)
            if (
                self.strict and first_word
                and any(first_word.group(1).casefold()[:5] in source for source in support)
            ):
                # Any word is capitalised at the start of a sentence. "Scaled dot-product ..."
                # is fine when the passage says "scale"; an unknown name is still checked.
                subject_terms.add(first_word.group(1).casefold())
            if not self._terms_are_supported(clean_sentence, support, subject_terms):
                return GateResult(
                    False,
                    "a quoted phrase or named term is absent from its cited passage",
                    None,
                    tuple(dict.fromkeys(cited)),
                )

            if self.strict:
                if not self._number_context_is_supported(clean_sentence, support):
                    return GateResult(
                        False,
                        "a number appears in its cited passage but not near the claim's subject",
                        None,
                        tuple(dict.fromkeys(cited)),
                    )
                if not self._units_are_supported(clean_sentence, support, question or ""):
                    return GateResult(
                        False,
                        "a number's unit differs from the unit in its cited passage",
                        None,
                        tuple(dict.fromkeys(cited)),
                    )
                if not self._negation_is_supported(clean_sentence, support):
                    return GateResult(
                        False,
                        "a negated claim is not matched by a negation in its cited passage",
                        None,
                        tuple(dict.fromkeys(cited)),
                    )

        rendered = CITATION.sub(lambda match: self._render_citation(match.group(1), chunks_by_id), answer)
        return GateResult(True, "all sentence citations passed", rendered, tuple(dict.fromkeys(cited)))

    @staticmethod
    def _sentences(answer: str) -> list[str]:
        return [part.strip() for part in SENTENCE_SPLIT.split(answer.strip()) if part.strip()]

    def _normalize(self, text: str) -> str:
        return THOUSANDS_COMMA.sub("", text) if self.strict else text

    def _source_variants(self, text: str) -> list[str]:
        """The passage as extracted; in strict mode also with line-break hyphens repaired.

        PDF extraction leaves "English-\nto-German" and "theoret-\nical". The
        first needs the hyphen kept and the second needs it dropped, so both
        repairs are offered as support.
        """
        if not self.strict:
            return [text.casefold()]
        normalized = self._normalize(text).casefold()
        variants = [normalized]
        for repaired in (
            LINE_BREAK_HYPHEN.sub("-", normalized),
            LINE_BREAK_HYPHEN.sub("", normalized),
        ):
            if repaired not in variants:
                variants.append(repaired)
        return variants

    @property
    def _number_pattern(self) -> re.Pattern[str]:
        return NUMBER_WITH_UNIT if self.strict else NUMBER

    def _numbers_are_supported(self, sentence: str, sources: Sequence[str]) -> bool:
        # In strict mode "20 %" and "20%" are the same number as "20".
        strip = (lambda number: number.rstrip("%")) if self.strict else (lambda number: number)
        numbers = [strip(number) for number in self._number_pattern.findall(sentence)]
        source_numbers = {
            strip(number.casefold())
            for source in sources
            for number in self._number_pattern.findall(source)
        }
        return all(number.casefold() in source_numbers for number in numbers)

    @staticmethod
    def _content_stems(sentence: str) -> set[str]:
        """Five-letter stems of the claim's content words ("trained" ~ "training")."""
        return {
            word.casefold()[:5]
            for word in WORD.findall(sentence)
            if word.casefold() not in _CONTEXT_STOPWORDS
        }

    def _number_context_is_supported(self, sentence: str, sources: Sequence[str]) -> bool:
        """Each number must sit near words the claim also uses, somewhere in a cited passage."""
        stems = self._content_stems(sentence)
        if not stems:
            return True
        needed = 1  # two was measured to reject short true answers ("It took 3.5 days.")
        for number in dict.fromkeys(self._number_pattern.findall(sentence)):
            supported = False
            for source in sources:
                for match in self._number_pattern.finditer(source):
                    if match.group(0) != number.casefold():
                        continue
                    window = source[
                        max(0, match.start() - self.context_window_chars):
                        match.end() + self.context_window_chars
                    ]
                    if sum(stem in window for stem in stems) >= needed:
                        supported = True
                        break
                if supported:
                    break
            if not supported:
                return False
        return True

    @staticmethod
    def _unit_of(match: re.Match[str]) -> tuple[str, list[str]]:
        suffix = (match.group(2) or "").casefold()
        tail = [word.casefold() for word in re.findall(r"[A-Za-z][\w]*", match.group(3) or "")]
        return suffix, tail

    def _units_are_supported(self, sentence: str, sources: Sequence[str], question: str = "") -> bool:
        """"160ms" must not become "160 seconds", nor "100000 steps" "100000 hours".

        A number-and-unit the person wrote in their own question ("at 12.5 Hz")
        is theirs, not a new claim, so repeating it is allowed.
        """
        asked = {
            (match.group(1).casefold(), *self._unit_of(match)[0:1], *self._unit_of(match)[1][:1])
            for match in NUMBER_AND_TAIL.finditer(self._normalize(question))
        }
        for claim in NUMBER_AND_TAIL.finditer(sentence):
            number = claim.group(1).casefold()
            suffix, tail = self._unit_of(claim)
            unit_word = next((word for word in tail[:1] if word in UNIT_WORDS), None)
            if not suffix and not unit_word:
                continue  # the claim states no unit, so there is nothing to compare
            if (number, suffix, *tail[:1]) in asked:
                continue
            occurrences = [
                self._unit_of(match)
                for source in sources
                for match in NUMBER_AND_TAIL.finditer(source)
                if match.group(1) == number
            ]
            if not occurrences:
                continue  # an absent number is already rejected elsewhere

            def agrees(source_suffix: str, source_tail: list[str]) -> bool:
                if suffix:
                    # "7B" needs "7B"; a spelled-out source ("7 billion") also agrees.
                    return source_suffix == suffix or (
                        not source_suffix and bool(source_tail)
                        and source_tail[0].startswith(suffix[0])
                    )
                if source_suffix:
                    # "160 milliseconds" agrees with "160ms"; "160 seconds" does not.
                    return unit_word.startswith(source_suffix[0])
                return unit_word.rstrip("s") in {word.rstrip("s") for word in source_tail}

            if not any(agrees(*occurrence) for occurrence in occurrences):
                return False
        return True

    def _negation_is_supported(self, sentence: str, sources: Sequence[str]) -> bool:
        """A negated claim needs a source sentence that is negated and shares its subject."""
        if not CLAIM_NEGATION.search(sentence):
            return True
        stems = self._content_stems(sentence)
        numbers = set(self._number_pattern.findall(sentence.casefold()))
        for source in sources:
            for source_sentence in SOURCE_SENTENCE_SPLIT.split(source):
                if not SOURCE_NEGATION.search(source_sentence):
                    continue
                if numbers and not numbers & set(self._number_pattern.findall(source_sentence)):
                    continue
                if not stems or any(stem in source_sentence for stem in stems):
                    return True
        return False

    @staticmethod
    def _terms_are_supported(
        sentence: str, sources: Sequence[str], subject_terms: Collection[str] = ()
    ) -> bool:
        combined = " ".join(" ".join(source.casefold().split()) for source in sources)
        quoted = QUOTED.findall(sentence)
        if any(
            not CitationGate._whole_phrase_supported(phrase, combined)
            for phrase in quoted
        ):
            return False
        proper_terms = PROPER_TERM.findall(sentence)
        return all(
            term.casefold() in subject_terms
            or CitationGate._whole_phrase_supported(term, combined)
            for term in proper_terms
        )

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
        # Extraction sometimes stores a table cell or a formula as the section name;
        # show a section only when it reads as words.
        wordy = len(re.findall(r"[A-Za-z]{3,}", section)) >= 1 and (
            sum(char.isalnum() or char.isspace() for char in section) >= 0.8 * max(1, len(section))
        )
        section_text = f", §{section}" if wordy and section != "Front matter" else ""
        return f"[{title}, arXiv:{versioned_id}, PDF p. {page}{section_text}]"
