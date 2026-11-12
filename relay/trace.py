"""Validated, append-only per-request trace files."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from relay.config import PROJECT_ROOT


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    start_ms: float = Field(ge=0)
    end_ms: float = Field(ge=0)
    kind: Literal["wait", "work", "retry"]
    attempt: int = Field(default=1, ge=1)
    model: str | None = None
    status: str | None = None
    ttft_ms: float | None = Field(default=None, ge=0)
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    cached_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def end_after_start(self) -> Span:
        if self.end_ms < self.start_ms:
            raise ValueError("span end_ms cannot precede start_ms")
        return self


class RequestTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    question_id: str | None = None
    question: str = Field(min_length=1)
    answer: str
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    scenario: str = Field(min_length=1)
    arrival: str
    deadline: str
    level: Literal["full", "lite", "extractive", "wait"]
    cache_hit: bool
    coalesced: bool
    spans: list[Span]
    gave_up: bool = False


def write_trace(trace: RequestTrace, directory: str | Path | None = None) -> Path:
    """Create one trace JSON; fail instead of replacing an existing request trace."""
    target_dir = Path(directory or PROJECT_ROOT / "results" / "traces")
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", trace.request_id)
    path = target_dir / f"{safe_id}.json"
    serialized = json.dumps(
        trace.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise FileExistsError(f"Trace already exists and is append-only: {path}") from exc
    return path
