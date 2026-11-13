"""BM25, optional semantic retrieval, reciprocal-rank fusion, and reranking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Mapping, Sequence

import numpy as np
from rank_bm25 import BM25Okapi


TOKEN_PATTERN = re.compile(r"[\w]+(?:[.-][\w]+)*", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(text)]


# Words that make a sentence a question but say nothing about its subject.
# Left in a BM25 query they match chatty passages (Moshi's appendix dialogues)
# ahead of the method sections; see analysis/naive_retriever.py.
QUESTION_WORDS = frozenset(
    """what is are was were does do did the a an of in on to for how many much which who
    say says about paper and it its with by at from that this""".split()
)
# A table whose decimal points were torn off by PDF extraction: "4.92" became
# "492" with a stray "." on a nearby line. Its numbers are wrong as printed.
_DETACHED_DECIMALS = re.compile(r"\n\.\n|\n\. \.|\. \.\n")


def content_query(question: str) -> str:
    """The question with its question words removed (the whole question if nothing is left)."""
    kept = [token for token in tokenize(question) if token not in QUESTION_WORDS]
    return " ".join(kept) or question


def is_damaged_table(chunk: Mapping[str, Any]) -> bool:
    return chunk.get("kind") == "table" and bool(
        _DETACHED_DECIMALS.search(str(chunk.get("text", "")))
    )


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Mapping[str, Any]
    score: float
    bm25_rank: int | None
    semantic_rank: int | None
    rerank_score: float | None = None
    bm25_score: float | None = None


@dataclass(frozen=True)
class RetrievalResult:
    setting: str
    query: str
    hits: tuple[RetrievalHit, ...]
    bm25_ms: float
    semantic_ms: float
    rerank_ms: float


class PaperRetriever:
    """Loads one chunk setting and optionally adds semantic search/reranking.

    Models are lazy-loaded so baseline tests and BM25-only retrieval do not
    download model weights. Pass injected encoders for deterministic tests.
    """

    def __init__(
        self,
        chunks: Sequence[Mapping[str, Any]],
        *,
        setting: str,
        top_k: int,
        rrf_k: int = 60,
        candidate_multiplier: int = 3,
        semantic_encoder: Any | None = None,
        semantic_model_name: str | None = None,
        reranker: Any | None = None,
        reranker_model_name: str | None = None,
    ) -> None:
        if not chunks:
            raise ValueError("retriever needs at least one chunk")
        if top_k < 1 or rrf_k < 1 or candidate_multiplier < 1:
            raise ValueError("top_k, rrf_k, and candidate_multiplier must be positive")
        self.setting = setting
        self.top_k = top_k
        self.rrf_k = rrf_k
        self.candidate_depth = max(top_k, top_k * candidate_multiplier)
        self.chunks = tuple(dict(chunk) for chunk in chunks)
        if any(not chunk.get("chunk_id") or not chunk.get("text") for chunk in self.chunks):
            raise ValueError("every chunk needs a chunk_id and non-empty text")
        self._index_by_id = {chunk["chunk_id"]: chunk for chunk in self.chunks}
        if len(self._index_by_id) != len(self.chunks):
            raise ValueError("chunk IDs must be unique within a setting")
        self._tokens = [tokenize(str(chunk["text"])) for chunk in self.chunks]
        self._bm25 = BM25Okapi(self._tokens)
        self._semantic_encoder = semantic_encoder
        self._semantic_model_name = semantic_model_name
        self._reranker = reranker
        self._reranker_model_name = reranker_model_name
        self._embeddings: np.ndarray | None = None

    @classmethod
    def from_jsonl(
        cls,
        setting: str,
        *,
        top_k: int,
        chunk_dir: str | Path,
        drop_damaged_tables: bool = False,
        **kwargs: Any,
    ) -> PaperRetriever:
        path = Path(chunk_dir) / f"{setting}.jsonl"
        chunks = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if drop_damaged_tables:
            chunks = [chunk for chunk in chunks if not is_damaged_table(chunk)]
        return cls(chunks, setting=setting, top_k=top_k, **kwargs)

    def get_chunk(self, chunk_id: str) -> Mapping[str, Any] | None:
        return self._index_by_id.get(chunk_id)

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        use_semantic: bool = False,
        rerank: bool = False,
        paper_ids: Collection[str] | None = None,
    ) -> RetrievalResult:
        """Search the index; ``paper_ids`` restricts results to those papers."""
        import time

        eligible = [
            index for index, chunk in enumerate(self.chunks)
            if not paper_ids or chunk.get("paper_id") in paper_ids
        ]
        if not eligible:
            return RetrievalResult(self.setting, query, (), 0.0, 0.0, 0.0)

        final_k = top_k or self.top_k
        if final_k < 1:
            raise ValueError("top_k must be positive")
        candidate_k = min(len(eligible), max(final_k, self.candidate_depth))
        bm25_start = time.perf_counter()
        scores = self._bm25.get_scores(tokenize(query))
        bm25_order = sorted(
            eligible,
            key=lambda index: (-float(scores[index]), str(self.chunks[index]["chunk_id"])),
        )[:candidate_k]
        bm25_ms = (time.perf_counter() - bm25_start) * 1000
        bm25_rank = {self.chunks[index]["chunk_id"]: rank for rank, index in enumerate(bm25_order, 1)}
        bm25_scores = {self.chunks[index]["chunk_id"]: float(scores[index]) for index in bm25_order}

        semantic_ms = 0.0
        semantic_rank: dict[str, int] = {}
        if use_semantic:
            semantic_start = time.perf_counter()
            encoder = self._get_semantic_encoder()
            embeddings = self._get_embeddings(encoder)
            query_embedding = np.asarray(
                encoder.encode([query], normalize_embeddings=True, convert_to_numpy=True)
            )[0]
            similarities = embeddings @ query_embedding
            semantic_order = sorted(
                eligible,
                key=lambda index: (
                    -float(similarities[index]),
                    str(self.chunks[index]["chunk_id"]),
                ),
            )[:candidate_k]
            semantic_rank = {
                self.chunks[index]["chunk_id"]: rank
                for rank, index in enumerate(semantic_order, 1)
            }
            semantic_ms = (time.perf_counter() - semantic_start) * 1000

        candidate_ids = set(bm25_rank) | set(semantic_rank)
        fused_scores: dict[str, float] = {}
        for chunk_id in candidate_ids:
            score = 0.0
            if chunk_id in bm25_rank:
                score += 1.0 / (self.rrf_k + bm25_rank[chunk_id])
            if chunk_id in semantic_rank:
                score += 1.0 / (self.rrf_k + semantic_rank[chunk_id])
            fused_scores[chunk_id] = score
        fused_order = sorted(candidate_ids, key=lambda chunk_id: (-fused_scores[chunk_id], chunk_id))
        rerank_ms = 0.0
        rerank_scores: dict[str, float] = {}
        if rerank:
            rerank_start = time.perf_counter()
            reranker = self._get_reranker()
            rerank_ids = fused_order[:candidate_k]
            pairs = [[query, str(self._index_by_id[item]["text"])] for item in rerank_ids]
            raw_scores = np.asarray(reranker.predict(pairs)).reshape(-1)
            rerank_scores = {
                chunk_id: float(raw_score)
                for chunk_id, raw_score in zip(rerank_ids, raw_scores, strict=True)
            }
            fused_order = sorted(
                rerank_ids,
                key=lambda chunk_id: (-rerank_scores[chunk_id], chunk_id),
            )
            rerank_ms = (time.perf_counter() - rerank_start) * 1000

        hits = tuple(
            RetrievalHit(
                chunk=self._index_by_id[chunk_id],
                score=fused_scores[chunk_id],
                bm25_rank=bm25_rank.get(chunk_id),
                semantic_rank=semantic_rank.get(chunk_id),
                rerank_score=rerank_scores.get(chunk_id),
                bm25_score=bm25_scores.get(chunk_id),
            )
            for chunk_id in fused_order[:final_k]
        )
        return RetrievalResult(self.setting, query, hits, bm25_ms, semantic_ms, rerank_ms)

    def _get_semantic_encoder(self) -> Any:
        if self._semantic_encoder is not None:
            return self._semantic_encoder
        if not self._semantic_model_name:
            raise RuntimeError("Semantic retrieval needs an encoder or configured model name")
        from sentence_transformers import SentenceTransformer

        self._semantic_encoder = SentenceTransformer(self._semantic_model_name, device="cpu")
        return self._semantic_encoder

    def _get_embeddings(self, encoder: Any) -> np.ndarray:
        if self._embeddings is None:
            self._embeddings = np.asarray(
                encoder.encode(
                    [str(chunk["text"]) for chunk in self.chunks],
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
            )
        return self._embeddings

    def _get_reranker(self) -> Any:
        if self._reranker is not None:
            return self._reranker
        if not self._reranker_model_name:
            raise RuntimeError("Reranking needs an injected reranker or configured model name")
        from sentence_transformers import CrossEncoder

        self._reranker = CrossEncoder(self._reranker_model_name, device="cpu")
        return self._reranker
