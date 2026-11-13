"""OpenAI-compatible async client with streaming and append-only usage ledger."""

from __future__ import annotations

import asyncio
import csv
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Mapping, Sequence

import httpx
from dotenv import load_dotenv

from relay.config import AppConfig, PROJECT_ROOT
from relay.limiter import AdaptiveLimiter, Admission


LEDGER_FIELDS = (
    "timestamp",
    "experiment",
    "model",
    "tokens_in",
    "tokens_out",
    "cached_tokens",
    "reasoning_tokens",
    "status",
    "headers_json",
)
MODEL_LIST_MODEL = "__models_endpoint__"


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    status_code: int
    headers: Mapping[str, str]
    usage: TokenUsage
    time_to_first_chunk_ms: float | None
    total_ms: float
    # "stop", or "length" when the reply was cut off by max_completion_tokens.
    finish_reason: str | None = None


@dataclass(frozen=True)
class ModelEstimate:
    requests: int
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if min(self.requests, self.input_tokens, self.output_tokens) < 0:
            raise ValueError("Experiment estimates cannot be negative")


class ExperimentApproval:
    """Single-experiment request and token allowance, consumed before each call."""

    __slots__ = ("__experiment", "__remaining", "__lock")

    def __init__(
        self,
        experiment: str,
        estimates: Mapping[str, ModelEstimate],
        *,
        _issuer: object | None = None,
    ) -> None:
        if _issuer is not _APPROVAL_ISSUER:
            raise PermissionError("Use approve_real_experiment() to create an approval")
        self.__experiment = experiment
        self.__remaining = dict(estimates)
        self.__lock = asyncio.Lock()

    async def reserve(
        self, experiment: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        async with self.__lock:
            if experiment != self.__experiment:
                raise RuntimeError("Approval is scoped to a different experiment")
            budget = self.__remaining.get(model)
            if budget is None or budget.requests < 1:
                raise RuntimeError(f"No approved requests remain for model {model}")
            if input_tokens > budget.input_tokens or output_tokens > budget.output_tokens:
                raise RuntimeError(f"Call estimate exceeds the approved budget for {model}")
            self.__remaining[model] = ModelEstimate(
                requests=budget.requests - 1,
                input_tokens=budget.input_tokens - input_tokens,
                output_tokens=budget.output_tokens - output_tokens,
            )


@dataclass
class StreamCapture:
    """Mutable result holder filled when a streaming iterator completes."""

    response: LLMResponse | None = None


class LLMRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        headers: Mapping[str, str],
        body: str,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = dict(headers)
        self.body = body


class RateLimitAdmissionError(RuntimeError):
    def __init__(self, admission: Admission) -> None:
        super().__init__(
            "local limiter denied request" + (
                f"; wait {admission.wait_seconds:.3f}s" if admission.wait_seconds is not None else ""
            )
        )
        self.admission = admission


_APPROVAL_ISSUER = object()
_RPD_SNAPSHOT_MAX_AGE = timedelta(minutes=1)


def last_known_daily_requests_remaining(
    ledger_path: str | Path, models: Sequence[str]
) -> dict[str, tuple[str, str | None]]:
    """Return each model's last RPD snapshot, marking old values as unknown."""
    unknown = {model: ("unknown (no recorded provider response)", None) for model in models}
    path = Path(ledger_path)
    if not path.exists():
        return unknown
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        observed: dict[str, tuple[str, str | None]] = {}
        for row in reversed(rows):
            model = row.get("model", "")
            if model not in unknown or model in observed:
                continue
            headers = json.loads(row.get("headers_json", "{}"))
            value = headers.get("x-ratelimit-remaining-requests")
            if value is not None:
                timestamp = row.get("timestamp")
                try:
                    parsed = datetime.fromisoformat(
                        (timestamp or "").replace("Z", "+00:00")
                    )
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    stale = datetime.now(timezone.utc) - parsed > _RPD_SNAPSHOT_MAX_AGE
                except ValueError:
                    stale = True
                observed[model] = (
                    f"unknown (stale; last observed {value})" if stale else str(value),
                    timestamp,
                )
        unknown.update(observed)
    except (OSError, json.JSONDecodeError, csv.Error):
        return {
            model: ("unknown (ledger could not be read)", None) for model in models
        }
    return unknown


def last_known_daily_tokens_remaining(
    ledger_path: str | Path, models: Sequence[str]
) -> dict[str, tuple[str, str | None]]:
    """Return a TPD snapshot when the provider recorded one; never infer it from TPM."""
    unknown = {model: ("unknown (no daily-token header recorded)", None) for model in models}
    path = Path(ledger_path)
    if not path.exists():
        return {model: ("unknown (no ledger)", None) for model in models}
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        observed: dict[str, tuple[str, str | None]] = {}
        for row in reversed(rows):
            model = row.get("model", "")
            if model not in unknown or model in observed:
                continue
            headers = json.loads(row.get("headers_json", "{}"))
            value = headers.get("x-ratelimit-remaining-tokens-day")
            if value is not None:
                timestamp = row.get("timestamp")
                try:
                    parsed = datetime.fromisoformat(
                        (timestamp or "").replace("Z", "+00:00")
                    )
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    stale = datetime.now(timezone.utc) - parsed > _RPD_SNAPSHOT_MAX_AGE
                except ValueError:
                    stale = True
                observed[model] = (
                    f"unknown (stale; last observed {value})" if stale else str(value),
                    timestamp,
                )
        unknown.update(observed)
    except (OSError, json.JSONDecodeError, csv.Error):
        return {model: ("unknown (ledger could not be read)", None) for model in models}
    return unknown


async def approve_real_experiment(
    experiment: str,
    estimates: Mapping[str, ModelEstimate],
    *,
    ledger_path: str | Path,
    public_tpd_reference: int | None = None,
) -> ExperimentApproval:
    """Print the experiment budget, then create a one-experiment permit on `go`."""
    if not experiment or not estimates:
        raise ValueError("An experiment name and at least one model estimate are required")
    print(f"Experiment: {experiment}")
    for model, estimate in estimates.items():
        print(
            f"  {model}: {estimate.requests} requests; "
            f"up to {estimate.input_tokens} conservative input-token upper bound and "
            f"{estimate.output_tokens} output tokens"
        )
    snapshots = last_known_daily_requests_remaining(ledger_path, list(estimates))
    token_snapshots = last_known_daily_tokens_remaining(ledger_path, list(estimates))
    for model in estimates:
        remaining, timestamp = snapshots[model]
        age = f" at {timestamp}" if timestamp else ""
        print(f"  {model}: last observed daily requests remaining: {remaining}{age}")
        token_remaining, token_timestamp = token_snapshots[model]
        token_age = f" at {token_timestamp}" if token_timestamp else ""
        print(f"  {model}: last observed daily tokens remaining: {token_remaining}{token_age}")
        if public_tpd_reference is not None:
            estimate = estimates[model]
            total = estimate.input_tokens + estimate.output_tokens
            headroom = public_tpd_reference - total
            print(
                f"  {model}: {total} estimated tokens versus {public_tpd_reference} "
                f"published free-plan TPD; reference headroom {headroom} "
                "(account-specific remaining quota may differ)"
            )
    print("These are per-model snapshots, not a live limit check.")
    response = await asyncio.to_thread(input, 'Type "go" to approve this experiment: ')
    if response.strip() != "go":
        raise PermissionError("Experiment was not approved; no provider call was made")
    return ExperimentApproval(experiment, estimates, _issuer=_APPROVAL_ISSUER)


class LLMClient:
    """Calls a local stand-in or Groq without choosing models implicitly.

    Groq requests require a one-experiment permit returned by
    approve_real_experiment. The permit enforces per-model request and token
    estimates and is consumed before each request.
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        ledger_path: str | Path | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        limiter: AdaptiveLimiter | None = None,
        reserve_bytes_per_token: float = 1.0,
    ) -> None:
        if reserve_bytes_per_token < 1:
            raise ValueError("reserve_bytes_per_token must be at least 1")
        # The approval budget (money) always uses the hard bound of one token per
        # byte. The local limiter (admission) may use a measured ratio instead, or
        # it refuses calls the provider would have accepted.
        self.reserve_bytes_per_token = reserve_bytes_per_token
        self.config = config
        self.ledger_path = Path(ledger_path or PROJECT_ROOT / "budget_ledger.csv")
        self._ledger_lock = asyncio.Lock()
        self.limiter = limiter
        self._client = httpx.AsyncClient(
            timeout=timeout or config.request_timeout_s,
            transport=transport,
        )
        load_dotenv(PROJECT_ROOT / ".env", override=False)

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_models(
        self,
        *,
        experiment: str,
        approval: ExperimentApproval,
    ) -> tuple[list[Mapping[str, Any]], Mapping[str, str]]:
        """Fetch model metadata through the same guarded client and ledger."""
        headers = self._headers()
        limiter_reservation = await self._reserve_limits(MODEL_LIST_MODEL, 0)
        try:
            await approval.reserve(experiment, MODEL_LIST_MODEL, 0, 0)
        except Exception:
            await self._release_limits(limiter_reservation)
            raise
        try:
            response = await self._client.get(
                f"{self.config.active_base_url}/models", headers=headers
            )
        except httpx.TimeoutException as exc:
            await self._reconcile_limits(limiter_reservation, {}, 0)
            await self._write_ledger(experiment, MODEL_LIST_MODEL, "timeout", {}, TokenUsage())
            raise TimeoutError("Model discovery request timed out") from exc
        except httpx.HTTPError as exc:
            await self._reconcile_limits(limiter_reservation, {}, 0)
            await self._write_ledger(experiment, MODEL_LIST_MODEL, "error", {}, TokenUsage())
            raise RuntimeError(f"Model discovery transport failed: {exc}") from exc

        response_headers = self._header_snapshot(response.headers)
        await self._reconcile_limits(
            limiter_reservation, response_headers, response.status_code
        )
        if response.is_error:
            status = "rate_limited" if response.status_code == 429 else "error"
            body = response.text
            await self._write_ledger(
                experiment, MODEL_LIST_MODEL, status, response_headers, TokenUsage()
            )
            raise LLMRequestError(
                f"Model discovery returned HTTP {response.status_code}",
                status_code=response.status_code,
                headers=response_headers,
                body=body,
            )
        try:
            data = response.json()
            models = data.get("data") if isinstance(data, Mapping) else None
            if not isinstance(models, list) or not all(
                isinstance(model, Mapping) and isinstance(model.get("id"), str)
                for model in models
            ):
                raise ValueError("Expected a data array of model objects")
        except (ValueError, json.JSONDecodeError) as exc:
            await self._write_ledger(
                experiment, MODEL_LIST_MODEL, "error", response_headers, TokenUsage()
            )
            raise LLMRequestError(
                "Model discovery returned malformed JSON",
                status_code=response.status_code,
                headers=response_headers,
                body=response.text,
            ) from exc
        await self._write_ledger(
            experiment, MODEL_LIST_MODEL, "ok", response_headers, TokenUsage()
        )
        return models, response_headers

    def _selected_model(self, model: str | None) -> str:
        selected = model or (
            self.config.groq.model if self.config.provider == "groq" else "standin"
        )
        if not selected:
            raise RuntimeError("No model selected; approve and complete decision 0002 first.")
        return selected

    @staticmethod
    def input_token_upper_bound(
        messages: Sequence[Mapping[str, Any]],
        *,
        model: str,
        stream: bool = False,
        **options: Any,
    ) -> int:
        """Expose the same preflight bound used by approval checks."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": stream,
            **options,
        }
        if stream:
            payload.setdefault("stream_options", {"include_usage": True})
        return LLMClient._input_token_upper_bound(payload)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.provider == "groq":
            key = self.config.api_key
            if key is None:
                raise RuntimeError("Groq API key is unavailable.")
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _payload(
        self,
        messages: Sequence[Mapping[str, Any]],
        model: str | None,
        stream: bool,
        options: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        reserved = {"model", "messages", "stream"}.intersection(options)
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(f"Request options cannot override {names}")
        selected_model = self._selected_model(model)
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": list(messages),
            "stream": stream,
            **options,
        }
        if stream:
            payload.setdefault("stream_options", {"include_usage": True})
        return selected_model, payload

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        experiment: str,
        model: str | None = None,
        approval: ExperimentApproval | None = None,
        **options: Any,
    ) -> LLMResponse:
        selected_model, payload = self._payload(messages, model, False, options)
        headers = self._headers()
        input_estimate = self._input_token_upper_bound(payload)
        output_limit = self._output_limit(payload)
        limiter_reservation = await self._reserve_limits(
            selected_model, math.ceil(input_estimate / self.reserve_bytes_per_token) + output_limit
        )
        try:
            await self._authorize(
                experiment, selected_model, approval, input_estimate, output_limit
            )
        except Exception:
            await self._release_limits(limiter_reservation)
            raise
        started = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self.config.active_base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
        except httpx.TimeoutException as exc:
            await asyncio.shield(self._reconcile_limits(limiter_reservation, {}, 0))
            await asyncio.shield(
                self._write_ledger(experiment, selected_model, "timeout", {}, TokenUsage())
            )
            raise TimeoutError("LLM request timed out") from exc
        except asyncio.CancelledError:
            # A request may have reached the provider before its caller's
            # deadline cancelled the HTTP task. Reconcile without headers so
            # local reservations are released and known capacity is reduced
            # conservatively instead of being stranded indefinitely.
            await asyncio.shield(self._reconcile_limits(limiter_reservation, {}, 0))
            await asyncio.shield(
                self._write_ledger(experiment, selected_model, "cancelled", {}, TokenUsage())
            )
            raise
        except httpx.HTTPError as exc:
            await asyncio.shield(self._reconcile_limits(limiter_reservation, {}, 0))
            await asyncio.shield(
                self._write_ledger(experiment, selected_model, "error", {}, TokenUsage())
            )
            raise RuntimeError(f"LLM transport failed: {exc}") from exc

        elapsed_ms = (time.perf_counter() - started) * 1000
        response_headers = self._header_snapshot(response.headers)
        await asyncio.shield(
            self._reconcile_limits(limiter_reservation, response_headers, response.status_code)
        )
        if response.is_error:
            status = "rate_limited" if response.status_code == 429 else "error"
            body = response.text
            await self._write_ledger(
                experiment, selected_model, status, response_headers, TokenUsage()
            )
            raise LLMRequestError(
                f"LLM returned HTTP {response.status_code}",
                status_code=response.status_code,
                headers=response_headers,
                body=body,
            )

        try:
            data = response.json()
            if not isinstance(data, Mapping):
                raise ValueError("Expected a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            body = response.text
            await self._write_ledger(
                experiment, selected_model, "error", response_headers, TokenUsage()
            )
            raise LLMRequestError(
                "LLM returned malformed JSON",
                status_code=response.status_code,
                headers=response_headers,
                body=body,
            ) from exc
        try:
            text = self._response_text(data)
            usage = self._usage(data.get("usage"))
            finish_reason = data["choices"][0].get("finish_reason")
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            await self._write_ledger(
                experiment, selected_model, "error", response_headers, TokenUsage()
            )
            raise LLMRequestError(
                "LLM returned an unexpected response shape",
                status_code=response.status_code,
                headers=response_headers,
                body=response.text,
            ) from exc
        await self._write_ledger(experiment, selected_model, "ok", response_headers, usage)
        return LLMResponse(
            text=text,
            model=selected_model,
            status_code=response.status_code,
            headers=response_headers,
            usage=usage,
            time_to_first_chunk_ms=None,
            total_ms=elapsed_ms,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )

    async def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        experiment: str,
        capture: StreamCapture,
        model: str | None = None,
        approval: ExperimentApproval | None = None,
        **options: Any,
    ) -> AsyncIterator[str]:
        selected_model, payload = self._payload(messages, model, True, options)
        headers = self._headers()
        input_estimate = self._input_token_upper_bound(payload)
        output_limit = self._output_limit(payload)
        limiter_reservation = await self._reserve_limits(
            selected_model, math.ceil(input_estimate / self.reserve_bytes_per_token) + output_limit
        )
        try:
            await self._authorize(
                experiment, selected_model, approval, input_estimate, output_limit
            )
        except Exception:
            await self._release_limits(limiter_reservation)
            raise
        started = time.perf_counter()
        first_chunk_ms: float | None = None
        chunks: list[str] = []
        usage = TokenUsage()
        finish_reason: str | None = None
        response_headers: dict[str, str] = {}
        status_code = 0
        ledger_status = "error"
        saw_done = False
        try:
            async with self._client.stream(
                "POST",
                f"{self.config.active_base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                status_code = response.status_code
                response_headers = self._header_snapshot(response.headers)
                if response.is_error:
                    body = await response.aread()
                    ledger_status = "rate_limited" if response.status_code == 429 else "error"
                    raise LLMRequestError(
                        f"LLM returned HTTP {response.status_code}",
                        status_code=response.status_code,
                        headers=response_headers,
                        body=body.decode("utf-8", errors="replace"),
                    )

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_line = line[len("data:") :].strip()
                    if data_line == "[DONE]":
                        saw_done = True
                        break
                    try:
                        event = json.loads(data_line)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError("Malformed JSON in LLM event stream") from exc
                    if not isinstance(event, Mapping):
                        raise RuntimeError("Malformed JSON object in LLM event stream")
                    if event.get("usage") is not None:
                        usage = self._usage(event["usage"])
                    for choice in event.get("choices", []):
                        if isinstance(choice.get("finish_reason"), str):
                            finish_reason = choice["finish_reason"]
                        content = choice.get("delta", {}).get("content")
                        if isinstance(content, str) and content:
                            if first_chunk_ms is None:
                                first_chunk_ms = (time.perf_counter() - started) * 1000
                            chunks.append(content)
                            yield content
                if not saw_done:
                    raise RuntimeError("LLM stream ended before the [DONE] event")
                ledger_status = "ok"
        except httpx.TimeoutException:
            ledger_status = "timeout"
            raise TimeoutError("LLM stream timed out")
        except httpx.HTTPError as exc:
            ledger_status = "error"
            raise RuntimeError(f"LLM stream transport failed: {exc}") from exc
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            await self._reconcile_limits(
                limiter_reservation, response_headers, status_code
            )
            await self._write_ledger(
                experiment, selected_model, ledger_status, response_headers, usage
            )
            capture.response = LLMResponse(
                text="".join(chunks),
                model=selected_model,
                status_code=status_code,
                headers=response_headers,
                usage=usage,
                time_to_first_chunk_ms=first_chunk_ms,
                total_ms=elapsed_ms,
                finish_reason=finish_reason,
            )

    async def _authorize(
        self,
        experiment: str,
        model: str,
        approval: ExperimentApproval | None,
        estimated_input_tokens: int,
        max_output_tokens: int,
    ) -> None:
        if self.config.provider != "groq":
            return
        if approval is None:
            raise PermissionError(
                "Groq calls require approval from approve_real_experiment() for this experiment."
            )
        if estimated_input_tokens <= 0 or max_output_tokens <= 0:
            raise ValueError("Real calls require a non-empty prompt and output-token limit")
        await approval.reserve(
            experiment, model, estimated_input_tokens, max_output_tokens
        )

    async def _reserve_limits(self, model: str, request_tokens: int) -> str | None:
        if self.limiter is None:
            return None
        admission = await self.limiter.reserve(model, request_tokens=request_tokens)
        if not admission.allowed:
            raise RateLimitAdmissionError(admission)
        return admission.reservation_id

    async def _release_limits(self, reservation_id: str | None) -> None:
        if self.limiter is not None and reservation_id is not None:
            await self.limiter.release(reservation_id)

    async def _reconcile_limits(
        self,
        reservation_id: str | None,
        headers: Mapping[str, str],
        status_code: int,
    ) -> None:
        if self.limiter is not None and reservation_id is not None:
            await self.limiter.reconcile(
                reservation_id, headers, status_code=status_code
            )

    @staticmethod
    def _input_token_upper_bound(payload: Mapping[str, Any]) -> int:
        """Bound all token-bearing request fields, excluding output-token controls."""
        input_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"max_completion_tokens", "max_tokens"}
        }
        messages = input_payload.get("messages", [])
        if not isinstance(messages, list) or not all(
            isinstance(message, Mapping)
            and (message.get("content") is None or isinstance(message.get("content"), str))
            for message in messages
        ):
            raise ValueError("Budgeted Groq calls support text-only messages")
        # The byte count is conservative for text tokenizers; the extra allowance
        # covers message framing and request-level input fields.
        serialized = json.dumps(
            input_payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return 16 + len(serialized)

    @staticmethod
    def _output_limit(payload: Mapping[str, Any]) -> int:
        if "max_completion_tokens" in payload and "max_tokens" in payload:
            raise ValueError("Specify only one output-token limit")
        choices = payload.get("n", 1)
        if isinstance(choices, bool) or not isinstance(choices, int) or choices != 1:
            raise ValueError("Budgeted requests require n=1")
        limit = payload.get("max_completion_tokens", payload.get("max_tokens"))
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError(
                "Every request must set a positive max_completion_tokens (or legacy max_tokens)"
            )
        return limit

    @staticmethod
    def _response_text(data: Mapping[str, Any]) -> str:
        choices = data.get("choices", [])
        if not isinstance(choices, list) or not choices:
            raise ValueError("choices must be a non-empty list")
        if not isinstance(choices[0], Mapping):
            raise ValueError("the first choice must be an object")
        message = choices[0].get("message", {})
        if not isinstance(message, Mapping):
            raise ValueError("the first choice message must be an object")
        content = message.get("content", "")
        if content is not None and not isinstance(content, str):
            raise ValueError("message content must be text")
        return content or ""

    @staticmethod
    def _usage(raw: Mapping[str, Any] | None) -> TokenUsage:
        if not isinstance(raw, Mapping):
            return TokenUsage()
        input_details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
        output_details = (
            raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
        )
        if not isinstance(input_details, Mapping):
            input_details = {}
        if not isinstance(output_details, Mapping):
            output_details = {}
        def optional_int(value: Any) -> int | None:
            return None if value is None else int(value)

        return TokenUsage(
            input_tokens=optional_int(raw.get("prompt_tokens", raw.get("input_tokens"))),
            output_tokens=optional_int(
                raw.get("completion_tokens", raw.get("output_tokens"))
            ),
            cached_tokens=optional_int(input_details.get("cached_tokens")),
            reasoning_tokens=optional_int(output_details.get("reasoning_tokens")),
        )

    @staticmethod
    def _header_snapshot(headers: Mapping[str, str]) -> dict[str, str]:
        return {str(key).lower(): str(value) for key, value in headers.items()}

    async def _write_ledger(
        self,
        experiment: str,
        model: str,
        status: str,
        headers: Mapping[str, str],
        usage: TokenUsage,
    ) -> None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        row = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "experiment": experiment,
            "model": model,
            "tokens_in": usage.input_tokens,
            "tokens_out": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            "status": status,
            "headers_json": json.dumps(dict(headers), sort_keys=True),
        }
        async with self._ledger_lock:
            await asyncio.to_thread(self._append_ledger_row, row)

    def _append_ledger_row(self, row: Mapping[str, Any]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not self.ledger_path.exists() or self.ledger_path.stat().st_size == 0
        with self.ledger_path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=LEDGER_FIELDS, lineterminator="\n")
            if needs_header:
                writer.writeheader()
            writer.writerow(row)
