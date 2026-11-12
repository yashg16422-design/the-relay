from __future__ import annotations

import asyncio
import json
import time

import httpx

from standin.server import create_app, load_config


def config(**overrides):
    raw = load_config().model_dump()
    for key, value in overrides.items():
        if key == "limits":
            raw["limits"].update(value)
        else:
            raw[key] = value
    return raw


async def post(app, payload):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://standin.test"
    ) as client:
        return await client.post("/v1/chat/completions", json=payload)


def payload(stream: bool = False):
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Where is the evidence?"}],
        "stream": stream,
        "max_completion_tokens": 20,
    }


def test_nonstream_returns_openai_shape_and_groq_style_headers():
    app = create_app(config())
    response = asyncio.run(post(app, payload()))

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "test-model"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["usage"]["prompt_tokens"] > 0
    assert body["usage"]["completion_tokens"] > 0
    assert response.headers["x-ratelimit-limit-requests"] == "1000"
    assert int(response.headers["x-ratelimit-remaining-requests"]) == 999
    assert response.headers["x-ratelimit-limit-tokens"] == "30000"
    assert "x-ratelimit-reset-tokens" in response.headers
    assert "retry-after" not in response.headers


def test_stream_emits_sse_chunks_usage_and_done():
    app = create_app(config(chunk_chars=5))
    response = asyncio.run(post(app, payload(stream=True)))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    events = [json.loads(line) for line in lines[:-1]]
    chunks = [
        choice["delta"]["content"]
        for event in events
        for choice in event.get("choices", [])
        if "content" in choice.get("delta", {})
    ]
    assert len(chunks) > 1
    usage = next(event["usage"] for event in events if event.get("usage"))
    assert usage["completion_tokens"] > 0


def test_rate_limit_returns_429_body_retry_after_and_zero_remaining():
    app = create_app(config(limits={"requests_per_minute": 1}))
    first = asyncio.run(post(app, payload()))
    second = asyncio.run(post(app, payload()))

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["x-ratelimit-remaining-requests-minute"] == "0"
    assert int(second.headers["retry-after"]) >= 1
    error = second.json()["error"]
    assert error["type"] == "rate_limit_error"
    assert error["code"] == "rate_limit_exceeded"


def test_intrinsically_oversized_request_gets_permanent_error_without_retry_after():
    app = create_app(config(limits={"tokens_per_minute": 1}))
    response = asyncio.run(post(app, payload()))

    assert response.status_code == 413
    assert "retry-after" not in response.headers
    assert response.json()["detail"]["code"] == "request_exceeds_token_window"


def test_token_estimate_includes_tools_and_schema_options():
    app = create_app(config(limits={"tokens_per_minute": 100}))
    ordinary = asyncio.run(post(app, payload()))
    large_options = payload()
    large_options["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "This schema text consumes prompt budget. " * 100,
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
        }
    ]
    large = asyncio.run(post(app, large_options))

    assert ordinary.status_code == 200
    assert large.status_code == 413
    assert "retry-after" not in large.headers


def test_output_token_cap_matches_reported_usage_not_character_count():
    app = create_app(config())
    long_prompt = payload()
    long_prompt["messages"] = [{"role": "user", "content": "x" * 500}]
    long_prompt["max_completion_tokens"] = 3
    response = asyncio.run(post(app, long_prompt))

    assert response.status_code == 200
    body = response.json()
    assert body["usage"]["completion_tokens"] <= 3
    assert body["choices"][0]["message"]["content"]


def test_injected_five_xx_has_rate_headers_and_openai_error_body():
    app = create_app(config(failure_5xx_probability=1.0, failure_5xx_status=503))
    response = asyncio.run(post(app, payload()))

    assert response.status_code == 503
    assert "x-ratelimit-limit-requests" in response.headers
    assert response.json()["error"]["code"] == "injected_failure"


def test_slow_success_and_hang_delay_are_deterministic_configured_paths():
    slow_app = create_app(
        config(slow_success_probability=1.0, slow_success_extra_ms=30)
    )
    started = time.perf_counter()
    slow_response = asyncio.run(post(slow_app, payload()))
    elapsed = (time.perf_counter() - started) * 1000
    assert slow_response.status_code == 200
    assert elapsed >= 20

    hang_app = create_app(config(hang_probability=1.0, hang_duration_ms=30))
    started = time.perf_counter()
    hang_response = asyncio.run(post(hang_app, payload()))
    elapsed = (time.perf_counter() - started) * 1000
    assert hang_response.status_code == 200
    assert elapsed >= 20


def test_response_slowdown_increases_with_inflight_load():
    app = create_app(config(base_delay_ms=40, load_delay_per_inflight_ms=80))

    async def concurrent_requests():
        return await asyncio.gather(post(app, payload()), post(app, payload()))

    started = time.perf_counter()
    responses = asyncio.run(concurrent_requests())
    elapsed = (time.perf_counter() - started) * 1000

    assert all(response.status_code == 200 for response in responses)
    assert elapsed >= 100


def test_streams_remain_inflight_until_the_response_body_finishes():
    app = create_app(
        config(chunk_chars=2, stream_chunk_delay_ms=15)
    )

    async def concurrent_streams():
        first = asyncio.create_task(post(app, payload(stream=True)))
        await asyncio.sleep(0.01)
        assert app.state.runtime.inflight == 1
        second = asyncio.create_task(post(app, payload(stream=True)))
        await asyncio.sleep(0.01)
        assert app.state.runtime.inflight == 2
        responses = await asyncio.gather(first, second)
        return responses

    responses = asyncio.run(concurrent_streams())
    assert all(response.status_code == 200 for response in responses)
    assert app.state.runtime.inflight == 0


def test_same_seed_repeats_injected_outcome_sequence():
    cfg = config(seed=42, failure_5xx_probability=0.5)

    async def sequence(app):
        statuses = []
        for _ in range(8):
            response = await post(app, payload())
            statuses.append(response.status_code)
        return statuses

    first = asyncio.run(sequence(create_app(cfg)))
    second = asyncio.run(sequence(create_app(cfg)))
    assert first == second
    assert 503 in first
    assert 200 in first


def test_admin_endpoint_changes_limits_midrun():
    app = create_app(config(limits={"requests_per_minute": 10}))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://standin.test"
        ) as client:
            before = await client.post("/v1/chat/completions", json=payload())
            update = await client.post(
                "/admin/limits", json={"requests_per_minute": 1}
            )
            after = await client.post("/v1/chat/completions", json=payload())
        return before, update, after

    before, update, after = asyncio.run(scenario())
    assert before.status_code == 200
    assert update.json()["limits"]["requests_per_minute"] == 1
    assert after.status_code == 429
