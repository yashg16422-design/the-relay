from __future__ import annotations

from relay.gate import CitationGate


def passage(chunk_id: str = "0123456789abcdef", text: str = "The Transformer achieved 28.4 BLEU."):
    return {
        "chunk_id": chunk_id,
        "paper_id": "1706.03762v7",
        "paper_title": "Attention Is All You Need",
        "page_number": 4,
        "section": "6.1 Results",
        "text": text,
    }


def test_valid_citation_is_rendered_with_exact_pdf_page() -> None:
    result = CitationGate().check(
        "The Transformer achieved 28.4 BLEU. [[cite:0123456789abcdef]]",
        retrieved_chunks=[passage()],
    )

    assert result.accepted is True
    assert "PDF p. 4" in result.rendered_answer
    assert "arXiv:1706.03762v7" in result.rendered_answer


def test_uncited_sentence_is_rejected() -> None:
    result = CitationGate().check(
        "The Transformer achieved 28.4 BLEU.", retrieved_chunks=[passage()]
    )

    assert result.accepted is False
    assert "no citation" in result.reason


def test_citation_must_be_from_retrieved_set() -> None:
    result = CitationGate().check(
        "The Transformer achieved 28.4 BLEU. [[cite:aaaaaaaaaaaaaaaa]]",
        retrieved_chunks=[passage()],
    )

    assert result.accepted is False
    assert "not in retrieved" in result.reason


def test_numeric_claim_must_appear_in_cited_text() -> None:
    result = CitationGate().check(
        "The Transformer achieved 99.9 BLEU. [[cite:0123456789abcdef]]",
        retrieved_chunks=[passage()],
    )

    assert result.accepted is False
    assert "number" in result.reason


def test_short_number_cannot_match_as_a_substring_of_larger_number() -> None:
    result = CitationGate().check(
        "The Transformer achieved 28 BLEU. [[cite:0123456789abcdef]]",
        retrieved_chunks=[passage(text="The Transformer achieved 128 BLEU.")],
    )

    assert result.accepted is False
    assert "number" in result.reason


def test_quoted_phrase_must_appear_in_cited_text() -> None:
    result = CitationGate().check(
        'The Transformer is "a recurrent network". [[cite:0123456789abcdef]]',
        retrieved_chunks=[passage()],
    )

    assert result.accepted is False
    assert "quoted phrase" in result.reason


def test_sentence_initial_unsupported_name_is_checked() -> None:
    result = CitationGate().check(
        "Xylophone uses attention. [[cite:0123456789abcdef]]",
        retrieved_chunks=[passage(text="The Transformer uses attention mechanisms.")],
    )

    assert result.accepted is False
    assert "quoted phrase" in result.reason


def test_every_sentence_requires_its_own_citation() -> None:
    result = CitationGate().check(
        "The Transformer uses attention. [[cite:0123456789abcdef]] It is fast.",
        retrieved_chunks=[passage(text="The Transformer uses attention.")],
    )

    assert result.accepted is False
    assert "no citation" in result.reason


def test_lowercase_followup_cannot_hide_inside_cited_sentence() -> None:
    result = CitationGate().check(
        "The Transformer uses attention. [[cite:0123456789abcdef]] it is magical.",
        retrieved_chunks=[passage(text="The Transformer uses attention.")],
    )

    assert result.accepted is False
    assert "no citation" in result.reason


def test_bracket_variants_of_the_citation_marker_are_accepted_but_still_checked() -> None:
    from relay.gate import normalize_citations

    for variant in ("【cite:0123456789abcdef】", "[[0123456789abcdef]]", "[cite:0123456789abcdef]"):
        answer = f"The Transformer achieved 28.4 BLEU {variant}."
        assert "[[cite:0123456789abcdef]]" in normalize_citations(answer)
        assert CitationGate().check(answer, retrieved_chunks=[passage()]).accepted is True
    unknown = "The Transformer achieved 28.4 BLEU 【cite:aaaaaaaaaaaaaaaa】."
    assert CitationGate().check(unknown, retrieved_chunks=[passage()]).accepted is False


def test_disclaimer_may_not_carry_a_claim_or_a_new_number() -> None:
    cited = "The Transformer achieved 28.4 BLEU. [[cite:0123456789abcdef]] "
    ok = cited + "The passages do not state the training cost."
    assert CitationGate().check(ok, retrieved_chunks=[passage()]).accepted is True
    for bad in (
        "The passages do not state the cost, but it was the cheapest model ever trained.",
        "The passages do not state the cost of 12 dollars.",
    ):
        assert CitationGate().check(cited + bad, retrieved_chunks=[passage()]).accepted is False
    # "1" is not allowed just because the question contains "12.5".
    assert CitationGate().check(
        cited + "The passages do not state value 1.", retrieved_chunks=[passage()],
        question="What happens at 12.5 Hz?",
    ).accepted is False
