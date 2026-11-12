from __future__ import annotations

import json

import numpy as np
import pytest

from relay.retrieval import PaperRetriever


def chunk(chunk_id: str, text: str, page: int) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "paper_id": "1706.03762v7",
        "paper_title": "Attention Is All You Need",
        "page_number": page,
        "page_start": page,
        "page_end": page,
        "section": "Test section",
        "kind": "text",
        "setting": "T",
        "text": text,
    }


class FakeEncoder:
    def encode(self, texts, **kwargs):
        vectors = []
        for text in texts:
            normalized = text.casefold()
            vectors.append(
                [1.0, 0.0] if "transformer" in normalized else [0.0, 1.0]
            )
        values = np.asarray(vectors, dtype=float)
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        return values / norms


class FakeReranker:
    def predict(self, pairs):
        return [10.0 if "attention" in text.casefold() else 0.0 for _, text in pairs]


def test_bm25_returns_cited_chunk_metadata_and_stable_order() -> None:
    retriever = PaperRetriever(
        [
            chunk("b", "The Transformer uses attention layers.", 4),
            chunk("a", "Recurrent networks process sequence tokens.", 2),
            chunk("c", "Convolutional sequence models use local filters.", 6),
            chunk("d", "The paper reports results on two translation tasks.", 8),
            chunk("e", "Training and inference use different resource settings.", 10),
        ],
        setting="R1",
        top_k=1,
    )

    result = retriever.search("Transformer attention", top_k=1)

    assert result.hits[0].chunk["chunk_id"] == "b"
    assert result.hits[0].chunk["page_number"] == 4
    assert result.hits[0].bm25_rank == 1
    assert result.semantic_ms == 0


def test_rrf_fuses_bm25_and_semantic_ranks() -> None:
    retriever = PaperRetriever(
        [
            chunk("bm25", "The transformer has attention mechanisms.", 1),
            chunk("semantic", "Architecture uses encoder and decoder blocks.", 2),
        ],
        setting="R2",
        top_k=2,
        semantic_encoder=FakeEncoder(),
        rrf_k=1,
        candidate_multiplier=1,
    )

    result = retriever.search("transformer architecture", use_semantic=True)

    assert {hit.chunk["chunk_id"] for hit in result.hits} == {"bm25", "semantic"}
    assert all(hit.bm25_rank is not None for hit in result.hits)
    assert all(hit.semantic_rank is not None for hit in result.hits)
    assert result.semantic_ms >= 0


def test_optional_cross_encoder_reranks_fused_candidates() -> None:
    retriever = PaperRetriever(
        [
            chunk("first", "The transformer attention architecture.", 1),
            chunk("second", "Other neural network details.", 2),
        ],
        setting="R3",
        top_k=2,
        reranker=FakeReranker(),
    )

    result = retriever.search("network details", rerank=True)

    assert result.hits[0].chunk["chunk_id"] == "first"
    assert result.hits[0].rerank_score == 10.0
    assert result.rerank_ms >= 0


def test_semantic_or_rerank_modes_fail_clearly_without_model_config() -> None:
    retriever = PaperRetriever([chunk("a", "One passage.", 1)], setting="R1", top_k=1)

    with pytest.raises(RuntimeError, match="Semantic retrieval needs"):
        retriever.search("passage", use_semantic=True)
    with pytest.raises(RuntimeError, match="Reranking needs"):
        retriever.search("passage", rerank=True)


def test_jsonl_loader_reads_setting_chunks(tmp_path) -> None:
    (tmp_path / "R1.jsonl").write_text(
        json.dumps(chunk("a", "Attention is useful.", 1)) + "\n", encoding="utf-8"
    )

    retriever = PaperRetriever.from_jsonl("R1", top_k=1, chunk_dir=tmp_path)

    assert retriever.get_chunk("a")["page_number"] == 1
    assert retriever.get_chunk("missing") is None


def test_duplicate_or_incomplete_chunks_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        PaperRetriever(
            [chunk("same", "one", 1), chunk("same", "two", 2)], setting="R1", top_k=1
        )
    with pytest.raises(ValueError, match="chunk_id"):
        PaperRetriever([{"chunk_id": "missing text"}], setting="R1", top_k=1)
