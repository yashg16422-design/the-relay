from __future__ import annotations

import asyncio
import csv
import json
from datetime import datetime, timezone

import httpx
import pytest

from relay.config import AppConfig
from relay.limiter import AdaptiveLimiter
from relay.llm_client import (
    ExperimentApproval,
    LLMClient,
    LLMRequestError,
    MODEL_LIST_MODEL,
    ModelEstimate,
    StreamCapture,
    approve_real_experiment,
    last_known_daily_tokens_remaining,
)


def app_config(provider: str = "standin") -> AppConfig:
    return AppConfig.model_validate(
        {
            "provider": provider,
            "request_timeout_s": 2,
            "stream": True,
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "api_key_env": "TEST_GROQ_KEY",
                "model": "test-model" if provider == "groq" else None,
            },
            "standin": {"base_url": "http://standin.test/v1"},
        }
    )


def ledger_rows(path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def test_daily_token_snapshot_does_not_confuse_tpm_with_tpd(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    path.write_text(
        "timestamp,experiment,model,tokens_in,tokens_out,cached_tokens,reasoning_tokens,status,headers_json\n"
        f'{datetime.now(timezone.utc).isoformat()},probe,test-model,1,1,,,ok,'
        '"{""x-ratelimit-remaining-tokens"":""7000""}"\n',
        encoding="utf-8",
    )

    snapshot = last_known_daily_tokens_remaining(path, ["test-model"])

    assert snapshot["test-model"][0] == "unknown (no daily-token header recorded)"


def test_daily_token_snapshot_reads_fresh_tpd_header(tmp_path) -> None:
    path = tmp_path / "ledger.csv"
    headers = json.dumps({"x-ratelimit-remaining-tokens-day": "123456"})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=("timestamp", "experiment", "model", "tokens_in", "tokens_out", "cached_tokens", "reasoning_tokens", "status", "headers_json"),
        )
        writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "experiment": "probe",
                "model": "test-model",
                "tokens_in": 1,
                "tokens_out": 1,
                "status": "ok",
                "headers_json": headers,
            }
        )

    snapshot = last_known_daily_tokens_remaining(path, ["test-model"])

    assert snapshot["test-model"][0] == "123456"


def real_payload_bound(messages, *, stream: bool = False, **options: object) -> int:
    payload: dict[str, object] = {
        "model": "test-model",
        "messages": list(messages),
        "stream": stream,
    }
    payload.update(options)
    if stream:
        payload.setdefault("stream_options", {"include_usage": True})
    return LLMClient._input_token_upper_bound(payload)


def test_nonstream_call_records_usage_and_headers(tmp_path) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"x-ratelimit-remaining-requests": "12"},
            json={
                "choices": [{"message": {"content": "grounded reply"}}],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 7,
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            },
        )

    async def call() -> None:
        async with LLMClient(
            app_config(), ledger_path=tmp_path / "ledger.csv", transport=httpx.MockTransport(handler)
        ) as client:
            result = await client.complete(
                [{"role": "user", "content": "question"}],
                experiment="unit",
                max_completion_tokens=12,
            )
        assert result.text == "grounded reply"
        assert result.usage.input_tokens == 21
        assert result.usage.output_tokens == 7
        assert result.usage.cached_tokens == 3
        assert result.usage.reasoning_tokens == 2
        assert result.headers["x-ratelimit-remaining-requests"] == "12"
        assert result.time_to_first_chunk_ms is None

    asyncio.run(call())
    assert seen["url"] == "http://standin.test/v1/chat/completions"
    assert seen["payload"]["stream"] is False
    rows = ledger_rows(tmp_path / "ledger.csv")
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["cached_tokens"] == "3"


def test_request_options_cannot_override_call_mode_or_model(tmp_path) -> None:
    async def call() -> None:
        async with LLMClient(app_config(), ledger_path=tmp_path / "ledger.csv") as client:
            with pytest.raises(ValueError, match="cannot override stream"):
                await client.complete(
                    [{"role": "user", "content": "question"}],
                    experiment="invalid-options",
                    stream=True,
                    max_completion_tokens=2,
                )

    asyncio.run(call())


def test_request_cannot_generate_multiple_choices_with_single_output_budget(tmp_path) -> None:
    async def call() -> None:
        async with LLMClient(app_config(), ledger_path=tmp_path / "ledger.csv") as client:
            with pytest.raises(ValueError, match="require n=1"):
                await client.complete(
                    [{"role": "user", "content": "question"}],
                    experiment="multiple-choice",
                    n=2,
                    max_completion_tokens=10,
                )

    asyncio.run(call())


def test_stream_yields_chunks_and_captures_final_usage(tmp_path) -> None:
    events = [
        {"choices": [{"delta": {"content": "first"}}]},
        {"choices": [{"delta": {"content": " second"}}]},
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 1},
            },
        },
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-trace": "abc"},
            content=body.encode(),
        )

    async def call() -> tuple[list[str], StreamCapture]:
        capture = StreamCapture()
        chunks: list[str] = []
        async with LLMClient(
            app_config(), ledger_path=tmp_path / "ledger.csv", transport=httpx.MockTransport(handler)
        ) as client:
            async for chunk in client.stream(
                [{"role": "user", "content": "question"}],
                experiment="stream-unit",
                capture=capture,
                max_completion_tokens=12,
            ):
                chunks.append(chunk)
        return chunks, capture

    chunks, capture = asyncio.run(call())
    assert chunks == ["first", " second"]
    assert capture.response is not None
    assert capture.response.text == "first second"
    assert capture.response.usage.input_tokens == 10
    assert capture.response.usage.output_tokens == 4
    assert capture.response.usage.cached_tokens == 1
    assert capture.response.headers["x-trace"] == "abc"
    assert seen["payload"]["stream_options"] == {"include_usage": True}
    rows = ledger_rows(tmp_path / "ledger.csv")
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"


def test_groq_call_is_blocked_without_per_experiment_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_GROQ_KEY", "dummy-test-key")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={})

    async def call() -> None:
        async with LLMClient(
            app_config("groq"), ledger_path=tmp_path / "ledger.csv", transport=httpx.MockTransport(handler)
        ) as client:
            await client.complete(
                [{"role": "user", "content": "test"}],
                experiment="must-not-send",
                max_completion_tokens=2,
            )

    with pytest.raises(PermissionError, match="approve_real_experiment"):
        asyncio.run(call())
    assert calls == []
    assert not (tmp_path / "ledger.csv").exists()


def test_chat_request_requires_model_decision_even_though_model_discovery_does_not(
    tmp_path,
) -> None:
    config = app_config("groq").model_copy(
        update={"groq": app_config("groq").groq.model_copy(update={"model": None})}
    )

    async def call() -> None:
        async with LLMClient(config, ledger_path=tmp_path / "ledger.csv") as client:
            with pytest.raises(RuntimeError, match="decision 0002"):
                client._selected_model(None)

    asyncio.run(call())


def test_model_discovery_requires_approval_and_uses_ledger(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_GROQ_KEY", "dummy-test-key")
    monkeypatch.setattr("builtins.input", lambda _: "go")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"x-ratelimit-remaining-requests": "9"},
            json={"data": [{"id": "model-a"}, {"id": "model-b"}]},
        )

    async def call() -> tuple[list[dict[str, str]], dict[str, str]]:
        ledger = tmp_path / "ledger.csv"
        approval = await approve_real_experiment(
            "model-list",
            {MODEL_LIST_MODEL: ModelEstimate(requests=1, input_tokens=0, output_tokens=0)},
            ledger_path=ledger,
        )
        async with LLMClient(
            app_config("groq"), ledger_path=ledger, transport=httpx.MockTransport(handler)
        ) as client:
            models, headers = await client.list_models(
                experiment="model-list", approval=approval
            )
        return list(models), dict(headers)

    models, headers = asyncio.run(call())
    assert [model["id"] for model in models] == ["model-a", "model-b"]
    assert headers["x-ratelimit-remaining-requests"] == "9"
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.groq.com/openai/v1/models"
    row = ledger_rows(tmp_path / "ledger.csv")[0]
    assert row["model"] == MODEL_LIST_MODEL
    assert row["status"] == "ok"


def test_scoped_approval_is_consumed_before_real_provider_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_GROQ_KEY", "dummy-test-key")
    monkeypatch.setattr("builtins.input", lambda _: "go")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "approved"}}], "usage": {}},
        )

    async def call() -> None:
        ledger = tmp_path / "ledger.csv"
        approval = await approve_real_experiment(
            "one-call-probe",
            {
                "test-model": ModelEstimate(
                    requests=1,
                    input_tokens=real_payload_bound(
                        [{"role": "user", "content": "tiny"}], max_completion_tokens=2
                    ),
                    output_tokens=2,
                )
            },
            ledger_path=ledger,
        )
        async with LLMClient(
            app_config("groq"), ledger_path=ledger, transport=httpx.MockTransport(handler)
        ) as client:
            result = await client.complete(
                [{"role": "user", "content": "tiny"}],
                experiment="one-call-probe",
                approval=approval,
                max_completion_tokens=2,
            )
            assert result.text == "approved"
            with pytest.raises(RuntimeError, match="No approved requests remain"):
                await client.complete(
                    [{"role": "user", "content": "second"}],
                    experiment="one-call-probe",
                    approval=approval,
                    max_completion_tokens=1,
                )

    asyncio.run(call())
    assert len(calls) == 1


def test_approval_is_scoped_to_experiment_and_model(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "go")

    async def reserve_wrong_experiment() -> None:
        approval = await approve_real_experiment(
            "experiment-a",
            {"model-a": ModelEstimate(1, 10, 5)},
            ledger_path="/missing/ledger.csv",
        )
        await approval.reserve("experiment-b", "model-a", 1, 1)

    with pytest.raises(RuntimeError, match="different experiment"):
        asyncio.run(reserve_wrong_experiment())


def test_approval_cannot_be_constructed_directly() -> None:
    with pytest.raises(PermissionError, match="approve_real_experiment"):
        ExperimentApproval("bypass", {"model": ModelEstimate(1, 10, 10)})


def test_approval_does_not_expose_mutable_budget(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: "go")

    async def get_approval() -> ExperimentApproval:
        return await approve_real_experiment(
            "sealed",
            {"model-a": ModelEstimate(1, 100, 10)},
            ledger_path="/missing/ledger.csv",
        )

    approval = asyncio.run(get_approval())
    assert not hasattr(approval, "remaining")


def test_approved_token_budget_caps_actual_prompt_and_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_GROQ_KEY", "dummy-test-key")
    monkeypatch.setattr("builtins.input", lambda _: "go")
    message = {"role": "user", "content": "a much longer prompt than approved"}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def call() -> None:
        ledger = tmp_path / "ledger.csv"
        approval = await approve_real_experiment(
            "bounded",
            {"test-model": ModelEstimate(requests=1, input_tokens=10, output_tokens=2)},
            ledger_path=ledger,
        )
        async with LLMClient(
            app_config("groq"), ledger_path=ledger, transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(RuntimeError, match="exceeds the approved budget"):
                await client.complete(
                    [message],
                    experiment="bounded",
                    approval=approval,
                    max_completion_tokens=3,
                )

    asyncio.run(call())
    assert requests == []


def test_malformed_success_response_is_recorded_as_error(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-trace": "bad-json"}, content=b"not-json")

    async def call() -> None:
        async with LLMClient(
            app_config(), ledger_path=tmp_path / "ledger.csv", transport=httpx.MockTransport(handler)
        ) as client:
            await client.complete(
                [{"role": "user", "content": "test"}],
                experiment="json-unit",
                max_completion_tokens=2,
            )

    with pytest.raises(LLMRequestError, match="malformed JSON"):
        asyncio.run(call())
    row = ledger_rows(tmp_path / "ledger.csv")[0]
    assert row["status"] == "error"
    assert row["tokens_in"] == ""
    assert row["tokens_out"] == ""
    assert json.loads(row["headers_json"])["x-trace"] == "bad-json"


@pytest.mark.parametrize(
    "body",
    [
        {"choices": "not-a-list"},
        {"choices": [{"message": {"content": "valid text"}}], "usage": {"prompt_tokens": "many"}},
    ],
)
def test_invalid_response_shape_or_usage_is_logged_with_unknown_counts(tmp_path, body) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-trace": "shape-check"}, json=body)

    async def call() -> None:
        async with LLMClient(
            app_config(), ledger_path=tmp_path / "ledger.csv", transport=httpx.MockTransport(handler)
        ) as client:
            await client.complete(
                [{"role": "user", "content": "test"}],
                experiment="shape-unit",
                max_completion_tokens=2,
            )

    with pytest.raises(LLMRequestError, match="unexpected response shape"):
        asyncio.run(call())
    row = ledger_rows(tmp_path / "ledger.csv")[0]
    assert row["status"] == "error"
    assert row["tokens_in"] == ""
    assert row["tokens_out"] == ""
    assert json.loads(row["headers_json"])["x-trace"] == "shape-check"


def test_stream_without_approval_does_not_write_provider_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_GROQ_KEY", "dummy-test-key")

    async def call() -> None:
        capture = StreamCapture()
        async with LLMClient(
            app_config("groq"), ledger_path=tmp_path / "ledger.csv"
        ) as client:
            async for _ in client.stream(
                [{"role": "user", "content": "test"}],
                experiment="blocked-stream",
                capture=capture,
                max_completion_tokens=2,
            ):
                pass

    with pytest.raises(PermissionError, match="approve_real_experiment"):
        asyncio.run(call())
    assert not (tmp_path / "ledger.csv").exists()


def test_429_is_logged_with_headers_and_raised(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "3"},
            json={"error": {"message": "rate limit"}},
        )

    async def call() -> None:
        async with LLMClient(
            app_config(), ledger_path=tmp_path / "ledger.csv", transport=httpx.MockTransport(handler)
        ) as client:
            await client.complete(
                [{"role": "user", "content": "test"}],
                experiment="limit-unit",
                max_completion_tokens=2,
            )

    with pytest.raises(LLMRequestError) as error:
        asyncio.run(call())
    assert error.value.status_code == 429
    assert error.value.headers["retry-after"] == "3"
    assert "rate limit" in error.value.body
    row = ledger_rows(tmp_path / "ledger.csv")[0]
    assert row["status"] == "rate_limited"
    assert row["tokens_in"] == ""
    assert row["tokens_out"] == ""
    assert json.loads(row["headers_json"])["retry-after"] == "3"


def test_client_reserves_observed_limit_and_reconciles_response_header(tmp_path) -> None:
    requests: list[httpx.Request] = []
    limiter = AdaptiveLimiter(
        additive_step=0.1, multiplicative_decrease=0.5, freshness_seconds=60
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-requests": "1",
                "x-ratelimit-remaining-requests": "0",
                "x-ratelimit-reset-requests": "60s",
            },
            json={"choices": [{"message": {"content": "first"}}]},
        )

    async def call() -> None:
        ledger = tmp_path / "ledger.csv"
        await limiter.set_observed_limit(
            "standin", "rpd", limit=1, remaining=1, reset_after_s=60
        )
        async with LLMClient(
            app_config(),
            ledger_path=ledger,
            transport=httpx.MockTransport(handler),
            limiter=limiter,
        ) as client:
            await client.complete(
                [{"role": "user", "content": "first"}],
                experiment="limited",
                max_completion_tokens=2,
            )
            with pytest.raises(RuntimeError, match="local limiter denied"):
                await client.complete(
                    [{"role": "user", "content": "second"}],
                    experiment="limited",
                    max_completion_tokens=2,
                )

    asyncio.run(call())
    assert len(requests) == 1


def test_cancelled_complete_conservatively_reconciles_limiter_reservation(tmp_path) -> None:
    class SlowTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(10)
            return httpx.Response(200, request=request, json={"choices": []})

    limiter = AdaptiveLimiter(
        additive_step=0.1, multiplicative_decrease=0.5, freshness_seconds=60
    )

    async def call() -> None:
        await limiter.set_observed_limit(
            "standin", "rpd", limit=10, remaining=10, reset_after_s=60
        )
        async with LLMClient(
            app_config(),
            ledger_path=tmp_path / "ledger.csv",
            transport=SlowTransport(),
            limiter=limiter,
        ) as client:
            request = asyncio.create_task(
                client.complete(
                    [{"role": "user", "content": "question"}],
                    experiment="cancel-test",
                    max_completion_tokens=2,
                )
            )
            await asyncio.sleep(0.01)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request

    asyncio.run(call())
    snapshot = asyncio.run(limiter.snapshots("standin"))
    assert snapshot["rpd"].remaining == 9
    rows = ledger_rows(tmp_path / "ledger.csv")
    assert len(rows) == 1
    assert rows[0]["status"] == "cancelled"


def test_input_budget_includes_tool_and_schema_options(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_GROQ_KEY", "dummy-test-key")
    monkeypatch.setattr("builtins.input", lambda _: "go")
    messages = [{"role": "user", "content": "small prompt"}]
    requests: list[httpx.Request] = []
    large_tool = {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search_papers",
                    "description": "detailed schema " * 100,
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": {"description": "schema " * 100}},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async def call() -> None:
        ledger = tmp_path / "ledger.csv"
        small_bound = real_payload_bound(messages, max_completion_tokens=2)
        approval = await approve_real_experiment(
            "schema-budget",
            {"test-model": ModelEstimate(1, small_bound, 2)},
            ledger_path=ledger,
        )
        async with LLMClient(
            app_config("groq"), ledger_path=ledger, transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(RuntimeError, match="exceeds the approved budget"):
                await client.complete(
                    messages,
                    experiment="schema-budget",
                    approval=approval,
                    max_completion_tokens=2,
                    **large_tool,
                )

    asyncio.run(call())
    assert requests == []


def test_stream_without_usage_records_unknown_token_counts(tmp_path) -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async def call() -> None:
        capture = StreamCapture()
        async with LLMClient(
            app_config(), ledger_path=tmp_path / "ledger.csv", transport=httpx.MockTransport(handler)
        ) as client:
            async for _ in client.stream(
                [{"role": "user", "content": "question"}],
                experiment="unknown-usage",
                capture=capture,
                max_completion_tokens=5,
            ):
                pass

    asyncio.run(call())
    row = ledger_rows(tmp_path / "ledger.csv")[0]
    assert row["status"] == "ok"
    assert row["tokens_in"] == ""
    assert row["tokens_out"] == ""
