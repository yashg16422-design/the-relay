"""OpenAI-compatible local stand-in with configurable latency and rate limits."""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Mapping

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field


STANDIN_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = STANDIN_DIR / "config.yaml"
MINUTE_WINDOW_S = 60.0
DAY_WINDOW_S = 24 * 60 * 60.0


class RateLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests_per_minute: int | None = Field(default=None, gt=0)
    tokens_per_minute: int | None = Field(default=None, gt=0)
    requests_per_day: int | None = Field(default=None, gt=0)
    tokens_per_day: int | None = Field(default=None, gt=0)


class StandinConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    token_bytes_per_token: int = Field(gt=0)
    base_delay_ms: float = Field(ge=0)
    load_delay_per_inflight_ms: float = Field(ge=0)
    slow_success_probability: float = Field(ge=0, le=1)
    slow_success_extra_ms: float = Field(ge=0)
    failure_5xx_probability: float = Field(ge=0, le=1)
    failure_5xx_status: int = Field(ge=500, le=599)
    hang_probability: float = Field(ge=0, le=1)
    hang_duration_ms: float = Field(ge=0)
    chunk_chars: int = Field(gt=0)
    stream_chunk_delay_ms: float = Field(ge=0)
    reply_prefix: str
    limits: RateLimits


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False


class LimitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests_per_minute: int | None = Field(default=None, gt=0)
    tokens_per_minute: int | None = Field(default=None, gt=0)
    requests_per_day: int | None = Field(default=None, gt=0)
    tokens_per_day: int | None = Field(default=None, gt=0)


def load_config(path: str | Path = DEFAULT_CONFIG) -> StandinConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, Mapping):
        raise ValueError(f"Stand-in config must be a mapping: {config_path}")
    return StandinConfig.model_validate(raw)


class RuntimeState:
    def __init__(self, config: StandinConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)
        self.rng_lock = Lock()
        self.lock = asyncio.Lock()
        self.inflight = 0
        self.next_request_id = 1
        self.request_minute: deque[float] = deque()
        self.token_minute: deque[tuple[float, int]] = deque()
        self.request_day: deque[float] = deque()
        self.token_day: deque[tuple[float, int]] = deque()

    def draw_scenario(self) -> tuple[float, float, float]:
        # Draw all outcomes together without an await, preserving reproducibility
        # for a fixed request order even when later handlers sleep concurrently.
        with self.rng_lock:
            return self.rng.random(), self.rng.random(), self.rng.random()

    async def next_id(self) -> int:
        async with self.lock:
            value = self.next_request_id
            self.next_request_id += 1
            return value

    async def enter(self) -> int:
        async with self.lock:
            self.inflight += 1
            return self.inflight

    async def leave(self) -> None:
        async with self.lock:
            self.inflight = max(0, self.inflight - 1)

    async def check_and_record(self, input_tokens: int, now: float) -> tuple[bool, dict[str, str], int]:
        async with self.lock:
            self._prune(now)
            limits = self.config.limits
            if (
                limits.tokens_per_minute is not None
                and input_tokens > limits.tokens_per_minute
            ) or (
                limits.tokens_per_day is not None
                and input_tokens > limits.tokens_per_day
            ):
                raise HTTPException(
                    status_code=413,
                    detail={
                        "type": "invalid_request_error",
                        "code": "request_exceeds_token_window",
                        "message": "This request cannot fit in the configured token window, even after reset.",
                    },
                )
            violations: list[float] = []
            checks = (
                (limits.requests_per_minute, len(self.request_minute), self.request_minute, MINUTE_WINDOW_S),
                (limits.requests_per_day, len(self.request_day), self.request_day, DAY_WINDOW_S),
            )
            for limit, used, bucket, window in checks:
                if limit is not None and used >= limit:
                    violations.append(self._wait_for(bucket[0], window, now))
            token_checks = (
                (limits.tokens_per_minute, self.token_minute, MINUTE_WINDOW_S),
                (limits.tokens_per_day, self.token_day, DAY_WINDOW_S),
            )
            for limit, bucket, window in token_checks:
                used = sum(count for _, count in bucket)
                if limit is not None and used + input_tokens > limit:
                    violations.append(self._wait_for(bucket[0][0], window, now) if bucket else 1.0)

            retry_after = max(1, math.ceil(max(violations))) if violations else 0
            if not violations:
                self.request_minute.append(now)
                self.request_day.append(now)
                self.token_minute.append((now, input_tokens))
                self.token_day.append((now, input_tokens))
            return bool(violations), self._headers(now), retry_after

    def _prune(self, now: float) -> None:
        while self.request_minute and now - self.request_minute[0] >= MINUTE_WINDOW_S:
            self.request_minute.popleft()
        while self.request_day and now - self.request_day[0] >= DAY_WINDOW_S:
            self.request_day.popleft()
        while self.token_minute and now - self.token_minute[0][0] >= MINUTE_WINDOW_S:
            self.token_minute.popleft()
        while self.token_day and now - self.token_day[0][0] >= DAY_WINDOW_S:
            self.token_day.popleft()

    @staticmethod
    def _wait_for(oldest: float, window: float, now: float) -> float:
        return max(0.0, oldest + window - now)

    @staticmethod
    def _reset_seconds(bucket: deque, window: float, now: float) -> str:
        if not bucket:
            return "0s"
        oldest = bucket[0][0] if isinstance(bucket[0], tuple) else bucket[0]
        return f"{max(0.0, oldest + window - now):.2f}s"

    def _headers(self, now: float) -> dict[str, str]:
        limits = self.config.limits
        request_minute_used = len(self.request_minute)
        request_day_used = len(self.request_day)
        token_minute_used = sum(count for _, count in self.token_minute)
        token_day_used = sum(count for _, count in self.token_day)

        def remaining(limit: int | None, used: int) -> str:
            return "unlimited" if limit is None else str(max(0, limit - used))

        def limit_value(limit: int | None) -> str:
            return "unlimited" if limit is None else str(limit)

        return {
            "x-ratelimit-limit-requests": limit_value(limits.requests_per_day),
            "x-ratelimit-remaining-requests": remaining(limits.requests_per_day, request_day_used),
            "x-ratelimit-reset-requests": self._reset_seconds(self.request_day, DAY_WINDOW_S, now),
            "x-ratelimit-limit-tokens": limit_value(limits.tokens_per_minute),
            "x-ratelimit-remaining-tokens": remaining(limits.tokens_per_minute, token_minute_used),
            "x-ratelimit-reset-tokens": self._reset_seconds(self.token_minute, MINUTE_WINDOW_S, now),
            "x-ratelimit-limit-requests-minute": limit_value(limits.requests_per_minute),
            "x-ratelimit-remaining-requests-minute": remaining(limits.requests_per_minute, request_minute_used),
            "x-ratelimit-reset-requests-minute": self._reset_seconds(self.request_minute, MINUTE_WINDOW_S, now),
            "x-ratelimit-limit-tokens-day": limit_value(limits.tokens_per_day),
            "x-ratelimit-remaining-tokens-day": remaining(limits.tokens_per_day, token_day_used),
            "x-ratelimit-reset-tokens-day": self._reset_seconds(self.token_day, DAY_WINDOW_S, now),
        }


def _approx_tokens(data: Any, bytes_per_token: int) -> int:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(1, math.ceil(len(encoded) / bytes_per_token))


def _request_input_payload(request: ChatRequest) -> dict[str, Any]:
    """Include all request fields that can add prompt tokens, excluding output caps."""
    return {
        key: value
        for key, value in request.model_dump(mode="json").items()
        if key not in {"max_completion_tokens", "max_tokens"}
    }


def _truncate_to_token_cap(text: str, cap: int, bytes_per_token: int) -> str:
    """Return the longest prefix whose reported approximation does not exceed cap."""
    if _approx_tokens(text, bytes_per_token) <= cap:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _approx_tokens(text[:middle], bytes_per_token) <= cap:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return ""


def _pipeline_reply(messages: list[dict[str, Any]]) -> str | None:
    """Produce deterministic, evidence-bound responses for Relay pipeline prompts.

    Ordinary OpenAI-compatible requests retain the configurable echo response.
    For draft calls, the claim is one verbatim sentence from a single passage;
    the only added text is its passage citation marker.
    """
    system = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "system"
    )
    if "Rewrite the user's question as one concise search query" in system:
        question = _last_user_text(messages).strip()
        return json.dumps({"query": question}, ensure_ascii=False)

    stage: str | None = None
    if "Decide whether the evidence contains enough information" in system:
        stage = "judge"
    elif "Answer only from the supplied passages" in system:
        stage = "draft"
    if stage is None:
        return None

    evidence_payload = _extract_evidence_payload(_last_user_text(messages))
    evidence = evidence_payload.get("evidence", []) if evidence_payload else []
    valid = [
        item for item in evidence
        if isinstance(item, dict)
        and isinstance(item.get("chunk_id"), str)
        and re.fullmatch(r"[0-9a-f]{16}", item["chunk_id"])
        and isinstance(item.get("text"), str)
        and item["text"].strip()
    ]
    if stage == "judge":
        return json.dumps(
            {"answerable": bool(valid), "reason": "supplied passage available" if valid else "no usable evidence"}
        )
    if not valid:
        return ""

    # The complete sentence is copied exactly, so all factual content remains
    # attributable to precisely one evidence chunk. Do not synthesize a claim.
    sentence_pattern = re.compile(r"[^.!?]+(?:[.!?](?=\s|$)|$)", re.DOTALL)
    for item in valid:
        match = sentence_pattern.search(item["text"].strip())
        sentence = match.group(0).strip() if match else item["text"].strip()
        if sentence:
            return f"{sentence} [[cite:{item['chunk_id']}]]"
    return ""


def _extract_evidence_payload(text: str) -> dict[str, Any] | None:
    """Read the JSON evidence object from a plain or repair-prefixed prompt."""
    decoder = json.JSONDecoder()
    for position, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("evidence"), list):
            return value
    return None


def create_app(config: StandinConfig | Mapping[str, Any] | None = None) -> FastAPI:
    if config is None:
        settings = load_config()
    elif isinstance(config, StandinConfig):
        settings = config
    else:
        settings = StandinConfig.model_validate(config)
    state = RuntimeState(settings)
    app = FastAPI(title="The Relay stand-in", version="0.1.0")
    app.state.runtime = state

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/admin/limits")
    async def update_limits(update: LimitUpdate) -> dict[str, Any]:
        changes = update.model_dump(exclude_unset=True)
        current = state.config.limits.model_dump()
        current.update(changes)
        state.config = state.config.model_copy(
            update={"limits": RateLimits.model_validate(current)}
        )
        return {"limits": state.config.limits.model_dump()}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: ChatRequest) -> Any:
        in_flight = await state.enter()
        handed_off_to_stream = False
        try:
            now = time.monotonic()
            cfg = state.config
            input_tokens = _approx_tokens(
                _request_input_payload(request), cfg.token_bytes_per_token
            )
            answer = _pipeline_reply(request.messages)
            if answer is None:
                answer = cfg.reply_prefix + _last_user_text(request.messages)
            request_options = request.model_extra or {}
            output_cap = request_options.get(
                "max_completion_tokens", request_options.get("max_tokens")
            )
            if output_cap is not None:
                if isinstance(output_cap, bool) or not isinstance(output_cap, int) or output_cap <= 0:
                    raise HTTPException(status_code=400, detail="Output-token limit must be a positive integer")
                answer = _truncate_to_token_cap(
                    answer, output_cap, cfg.token_bytes_per_token
                )
            choices = request_options.get("n", 1)
            if isinstance(choices, bool) or not isinstance(choices, int) or choices != 1:
                raise HTTPException(status_code=400, detail="The stand-in supports n=1 only")
            output_tokens = _approx_tokens(answer, cfg.token_bytes_per_token)
            limited, rate_headers, retry_after = await state.check_and_record(
                input_tokens + output_tokens, now
            )
            if limited:
                rate_headers["retry-after"] = str(retry_after)
                return JSONResponse(
                    status_code=429,
                    headers=rate_headers,
                    content={
                        "error": {
                            "message": "Rate limit reached in stand-in service",
                            "type": "rate_limit_error",
                            "code": "rate_limit_exceeded",
                        }
                    },
                )

            fail_draw, hang_draw, slow_draw = state.draw_scenario()
            delay_ms = cfg.base_delay_ms + max(0, in_flight - 1) * cfg.load_delay_per_inflight_ms
            if slow_draw < cfg.slow_success_probability:
                delay_ms += cfg.slow_success_extra_ms
            if hang_draw < cfg.hang_probability:
                delay_ms += cfg.hang_duration_ms
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000)

            request_id = await state.next_id()
            created = int(datetime.now(timezone.utc).timestamp())
            if fail_draw < cfg.failure_5xx_probability:
                return JSONResponse(
                    status_code=cfg.failure_5xx_status,
                    headers=rate_headers,
                    content={
                        "error": {
                            "message": "Injected stand-in server failure",
                            "type": "server_error",
                            "code": "injected_failure",
                        }
                    },
                )

            usage = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
            if request.stream:
                async def stream_body():
                    try:
                        async for event in _stream_events(
                            request_id,
                            created,
                            request.model,
                            answer,
                            usage,
                            cfg.chunk_chars,
                            cfg.stream_chunk_delay_ms,
                        ):
                            yield event
                    finally:
                        await state.leave()

                response = StreamingResponse(
                    stream_body(),
                    media_type="text/event-stream",
                    headers={**rate_headers, "cache-control": "no-cache", "connection": "keep-alive"},
                )
                handed_off_to_stream = True
                return response
            return JSONResponse(
                status_code=200,
                headers=rate_headers,
                content={
                    "id": f"chatcmpl-standin-{request_id}",
                    "object": "chat.completion",
                    "created": created,
                    "model": request.model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                },
            )
        finally:
            if not handed_off_to_stream:
                await state.leave()

    return app


async def _stream_events(
    request_id: int,
    created: int,
    model: str,
    text: str,
    usage: Mapping[str, int],
    chunk_chars: int,
    stream_chunk_delay_ms: float,
):
    yield f"data: {json.dumps({'id': f'chatcmpl-standin-{request_id}', 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
    for start in range(0, len(text), chunk_chars):
        delta = text[start : start + chunk_chars]
        payload = {
            "id": f"chatcmpl-standin-{request_id}",
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        await asyncio.sleep(stream_chunk_delay_ms / 1000)
    finish = {
        "id": f"chatcmpl-standin-{request_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    usage_event = {
        "id": f"chatcmpl-standin-{request_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
        "usage": dict(usage),
    }
    yield f"data: {json.dumps(finish)}\n\n"
    yield f"data: {json.dumps(usage_event)}\n\n"
    yield "data: [DONE]\n\n"


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("standin.server:app", host="127.0.0.1", port=8000, reload=False)
