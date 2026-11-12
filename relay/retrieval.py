"""BM25, optional semantic retrieval, reciprocal-rank fusion, and reranking."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from rank_bm25 import BM25Okapi


TOKEN_PATTERN = re.compile(r"[\w]+(?:[.-][\w]+)*", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(text)]


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Mapping[str, Any]
    score: float
    bm25_rank: int | None
    semantic_rank: int | None
    rerank_score: float | None = None


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
        **kwargs: Any,
    ) -> PaperRetriever:
        path = Path(chunk_dir) / f"{setting}.jsonl"
        chunks = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
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
    ) -> RetrievalResult:
        import time

        final_k = top_k or self.top_k
        if final_k < 1:
            raise ValueError("top_k must be positive")
        candidate_k = min(len(self.chunks), max(final_k, self.candidate_depth))
        bm25_start = time.perf_counter()
        scores = self._bm25.get_scores(tokenize(query))
        bm25_order = sorted(
            range(len(self.chunks)),
            key=lambda index: (-float(scores[index]), str(self.chunks[index]["chunk_id"])),
        )[:candidate_k]
        bm25_ms = (time.perf_counter() - bm25_start) * 1000
        bm25_rank = {self.chunks[index]["chunk_id"]: rank for rank, index in enumerate(bm25_order, 1)}

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
                range(len(self.chunks)),
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
