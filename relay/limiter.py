"""Header-driven limit snapshots with atomic reservations and AIMD feedback."""

from __future__ import annotations

import asyncio
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Literal, Mapping


BucketName = Literal["rpm", "tpm", "rpd", "tpd"]


@dataclass(frozen=True)
class BucketSnapshot:
    name: BucketName
    limit: int | None
    remaining: int | None
    reset_at: float | None
    observed_at: float
    source: str


@dataclass(frozen=True)
class Admission:
    allowed: bool
    blocked_by: tuple[str, ...] = ()
    wait_seconds: float | None = None
    unknown_buckets: tuple[str, ...] = ()
    reservation_id: str | None = None


def parse_duration(value: str) -> float | None:
    """Parse provider windows such as `2m59.56s` or simple decimal seconds."""
    text = value.strip().lower()
    try:
        seconds = float(text)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        pass
    # "ms" must be tried before "m" and "s": Groq reports short resets as e.g. "667ms".
    matches = list(re.finditer(r"(\d+(?:\.\d+)?)(ms|d|h|m|s)", text))
    if not matches or "".join(match.group(0) for match in matches) != text:
        return None
    factors = {"d": 86400, "h": 3600, "m": 60, "s": 1, "ms": 0.001}
    return sum(float(match.group(1)) * factors[match.group(2)] for match in matches)


def _retry_after_seconds(value: str, now_epoch: float) -> float | None:
    duration = parse_duration(value)
    if duration is not None:
        return duration
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        seconds = date.timestamp() - now_epoch
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


class AdaptiveLimiter:
    """Observe model-specific headers and reserve known capacity atomically.

    Buckets absent from headers or too old to trust remain unknown. No provider
    limits are invented. AIMD only scales a caller-supplied concurrency ceiling.
    """

    HEADER_KEYS: Mapping[BucketName, tuple[str, str, str]] = {
        "rpm": (
            "x-ratelimit-limit-requests-minute",
            "x-ratelimit-remaining-requests-minute",
            "x-ratelimit-reset-requests-minute",
        ),
        "tpm": (
            "x-ratelimit-limit-tokens",
            "x-ratelimit-remaining-tokens",
            "x-ratelimit-reset-tokens",
        ),
        "rpd": (
            "x-ratelimit-limit-requests",
            "x-ratelimit-remaining-requests",
            "x-ratelimit-reset-requests",
        ),
        "tpd": (
            "x-ratelimit-limit-tokens-day",
            "x-ratelimit-remaining-tokens-day",
            "x-ratelimit-reset-tokens-day",
        ),
    }

    def __init__(
        self,
        *,
        additive_step: float,
        multiplicative_decrease: float,
        freshness_seconds: float,
        continuous_refill: bool = False,
    ) -> None:
        if not math.isfinite(additive_step) or not 0 < additive_step <= 1:
            raise ValueError("additive_step must be finite and in (0, 1]")
        if not math.isfinite(multiplicative_decrease) or not 0 < multiplicative_decrease < 1:
            raise ValueError("multiplicative_decrease must be finite and in (0, 1)")
        if not math.isfinite(freshness_seconds) or freshness_seconds <= 0:
            raise ValueError("freshness_seconds must be finite and positive")
        self.additive_step = additive_step
        self.multiplicative_decrease = multiplicative_decrease
        self.freshness_seconds = freshness_seconds
        # False = treat the reset header as "the whole bucket comes back then".
        # True = the bucket refills steadily, which is what 332 real responses
        # show (results/raw/limit_refill_analysis.json): reset = used / refill rate.
        self.continuous_refill = continuous_refill
        self._buckets: dict[str, dict[BucketName, BucketSnapshot]] = {}
        self._cooldown_until: dict[str, float] = {}
        self._concurrency_factor: dict[str, float] = {}
        self._reservations: dict[str, tuple[str, dict[BucketName, int]]] = {}
        self._reserved: dict[str, dict[BucketName, int]] = {}
        self._lock = asyncio.Lock()

    async def observe(
        self,
        model: str,
        headers: Mapping[str, str],
        *,
        status_code: int,
        now_monotonic: float | None = None,
        now_epoch: float | None = None,
    ) -> None:
        monotonic, epoch = self._now(now_monotonic, now_epoch)
        normalized = self._normalize_headers(headers)
        async with self._lock:
            self._apply_headers_locked(model, normalized, monotonic)
            self._apply_feedback_locked(model, normalized, status_code, monotonic, epoch)

    async def reserve(
        self,
        model: str,
        *,
        request_tokens: int,
        now_monotonic: float | None = None,
    ) -> Admission:
        if isinstance(request_tokens, bool) or not isinstance(request_tokens, int) or request_tokens < 0:
            raise ValueError("request_tokens must be a non-negative integer")
        now = now_monotonic if now_monotonic is not None else asyncio.get_running_loop().time()
        async with self._lock:
            cooldown = self._cooldown_until.get(model)
            if cooldown is not None and now < cooldown:
                return Admission(False, ("retry-after",), cooldown - now)
            if cooldown is not None and now >= cooldown:
                self._cooldown_until.pop(model, None)

            costs: dict[BucketName, int] = {
                "rpm": 1,
                "rpd": 1,
                "tpm": request_tokens,
                "tpd": request_tokens,
            }
            buckets = self._buckets.get(model, {})
            reserved = self._reserved.setdefault(model, {})
            blocked: list[str] = []
            unknown: list[str] = []
            waits: list[float] = []
            normalized_costs: dict[BucketName, int] = {}

            for name, cost in costs.items():
                snapshot = buckets.get(name)
                if snapshot is None or snapshot.remaining is None:
                    unknown.append(name)
                    continue
                if now - snapshot.observed_at > self.freshness_seconds:
                    unknown.append(name)
                    continue
                remaining, rate = self._remaining_and_rate(snapshot, now)
                if remaining is None:
                    unknown.append(name)
                    continue
                available = remaining - reserved.get(name, 0)
                if available < cost:
                    blocked.append(name)
                    if rate:
                        # Wait only until the bucket holds this request, not until it is full.
                        waits.append((cost - available) / rate)
                    elif snapshot.reset_at is not None and snapshot.reset_at > now:
                        waits.append(snapshot.reset_at - now)
                else:
                    normalized_costs[name] = cost

            if blocked:
                return Admission(
                    False,
                    tuple(blocked),
                    max(waits) if waits else None,
                    tuple(unknown),
                )

            reservation_id = uuid.uuid4().hex
            self._reservations[reservation_id] = (model, normalized_costs)
            for name, cost in normalized_costs.items():
                reserved[name] = reserved.get(name, 0) + cost
            return Admission(True, unknown_buckets=tuple(unknown), reservation_id=reservation_id)

    def _remaining_and_rate(
        self, snapshot: BucketSnapshot, now: float
    ) -> tuple[float | None, float | None]:
        """What the bucket holds now, and its refill rate per second when that is known."""
        remaining: float | None = snapshot.remaining
        if self.continuous_refill and snapshot.limit is not None and snapshot.remaining is not None:
            # Refill rate: read it from the reset header when the bucket is not full
            # (reset = used / rate); otherwise it is the limit spread over its window.
            window = 60.0 if snapshot.name in ("rpm", "tpm") else 86400.0
            rate = snapshot.limit / window
            if (
                snapshot.reset_at is not None
                and snapshot.reset_at > snapshot.observed_at
                and snapshot.limit > snapshot.remaining
            ):
                rate = (snapshot.limit - snapshot.remaining) / (snapshot.reset_at - snapshot.observed_at)
            refilled = snapshot.remaining + rate * max(0.0, now - snapshot.observed_at)
            return min(float(snapshot.limit), refilled), rate
        if snapshot.reset_at is not None and now >= snapshot.reset_at:
            remaining = snapshot.limit if snapshot.limit is not None else None
        return remaining, None

    async def predicted_wait(
        self, model: str, *, request_tokens: int, now_monotonic: float | None = None
    ) -> tuple[float, str | None]:
        """Seconds until `request_tokens` would be admitted, and the bucket that decides it.

        Reserves nothing. Unknown or stale buckets count as no wait: the limiter
        does not invent a limit it has not seen.
        """
        now = now_monotonic if now_monotonic is not None else asyncio.get_running_loop().time()
        async with self._lock:
            worst, worst_name = 0.0, None
            cooldown = self._cooldown_until.get(model)
            if cooldown is not None and cooldown > now:
                worst, worst_name = cooldown - now, "retry-after"
            reserved = self._reserved.get(model, {})
            for name, snapshot in self._buckets.get(model, {}).items():
                if snapshot.remaining is None or now - snapshot.observed_at > self.freshness_seconds:
                    continue
                remaining, rate = self._remaining_and_rate(snapshot, now)
                if remaining is None:
                    continue
                cost = request_tokens if name in ("tpm", "tpd") else 1
                deficit = cost - (remaining - reserved.get(name, 0))
                if deficit <= 0:
                    continue
                if rate:
                    wait = deficit / rate
                elif snapshot.reset_at is not None and snapshot.reset_at > now:
                    wait = snapshot.reset_at - now
                else:
                    continue
                if wait > worst:
                    worst, worst_name = wait, name
            return worst, worst_name

    async def admission(
        self,
        model: str,
        *,
        request_tokens: int,
        now_monotonic: float | None = None,
    ) -> Admission:
        """Atomic admission alias retained for callers using the policy name."""
        return await self.reserve(
            model, request_tokens=request_tokens, now_monotonic=now_monotonic
        )

    async def reconcile(
        self,
        reservation_id: str,
        headers: Mapping[str, str],
        *,
        status_code: int,
        now_monotonic: float | None = None,
        now_epoch: float | None = None,
    ) -> None:
        monotonic, epoch = self._now(now_monotonic, now_epoch)
        normalized = self._normalize_headers(headers)
        async with self._lock:
            try:
                model, costs = self._reservations.pop(reservation_id)
            except KeyError as exc:
                raise KeyError(f"unknown or already reconciled reservation {reservation_id}") from exc
            reserved = self._reserved.get(model, {})
            buckets = self._buckets.setdefault(model, {})
            for name, cost in costs.items():
                reserved[name] = max(0, reserved.get(name, 0) - cost)
                if cost == 0:
                    continue
                header_keys = self.HEADER_KEYS[name]
                if header_keys[1] not in normalized:
                    old = buckets.get(name)
                    if old is not None and old.remaining is not None:
                        # The request may have consumed capacity despite missing headers.
                        buckets[name] = BucketSnapshot(
                            name=name,
                            limit=old.limit,
                            remaining=max(0, old.remaining - cost),
                            reset_at=old.reset_at,
                            observed_at=monotonic,
                            source="local-conservative-reservation",
                        )
            self._apply_headers_locked(
                model,
                normalized,
                monotonic,
                preserve_missing={name for name, cost in costs.items() if cost > 0},
            )
            self._apply_feedback_locked(model, normalized, status_code, monotonic, epoch)

    async def release(self, reservation_id: str) -> None:
        """Release a reservation only when no provider request was sent."""
        async with self._lock:
            try:
                model, costs = self._reservations.pop(reservation_id)
            except KeyError as exc:
                raise KeyError(f"unknown or already reconciled reservation {reservation_id}") from exc
            reserved = self._reserved.get(model, {})
            for name, cost in costs.items():
                reserved[name] = max(0, reserved.get(name, 0) - cost)

    async def set_observed_limit(
        self,
        model: str,
        bucket: BucketName,
        *,
        limit: int,
        remaining: int,
        reset_after_s: float | None,
        now_monotonic: float | None = None,
        source: str = "measurement",
    ) -> None:
        if limit < 0 or remaining < 0:
            raise ValueError("limit and remaining values cannot be negative")
        now = now_monotonic if now_monotonic is not None else asyncio.get_running_loop().time()
        async with self._lock:
            self._buckets.setdefault(model, {})[bucket] = BucketSnapshot(
                name=bucket,
                limit=limit,
                remaining=remaining,
                reset_at=now + reset_after_s if reset_after_s is not None else None,
                observed_at=now,
                source=source,
            )

    async def effective_concurrency(self, model: str, configured_ceiling: int) -> int:
        if configured_ceiling < 1:
            raise ValueError("configured_ceiling must be positive")
        async with self._lock:
            factor = self._concurrency_factor.get(model, 1.0)
            return max(1, math.floor(configured_ceiling * factor))

    async def snapshots(self, model: str) -> dict[BucketName, BucketSnapshot]:
        async with self._lock:
            return dict(self._buckets.get(model, {}))

    def _apply_headers_locked(
        self,
        model: str,
        headers: Mapping[str, str],
        now: float,
        *,
        preserve_missing: set[BucketName] | None = None,
    ) -> None:
        buckets = self._buckets.setdefault(model, {})
        preserve_missing = preserve_missing or set()
        for name, (limit_key, remaining_key, reset_key) in self.HEADER_KEYS.items():
            old = buckets.get(name)
            limit = self._positive_int(headers.get(limit_key))
            remaining = self._nonnegative_int(headers.get(remaining_key))
            reset_duration = parse_duration(headers[reset_key]) if reset_key in headers else None
            if limit is None and remaining is None and reset_duration is None:
                if old is not None and name not in preserve_missing:
                    buckets[name] = BucketSnapshot(
                        name=name,
                        limit=old.limit,
                        remaining=None,
                        reset_at=old.reset_at,
                        observed_at=now,
                        source="missing-or-invalid-header",
                    )
                continue
            buckets[name] = BucketSnapshot(
                name=name,
                limit=limit if limit is not None else (old.limit if old else None),
                remaining=remaining,
                reset_at=(now + reset_duration) if reset_duration is not None else (old.reset_at if old else None),
                observed_at=now,
                source="provider-header",
            )

    def _apply_feedback_locked(
        self,
        model: str,
        headers: Mapping[str, str],
        status_code: int,
        monotonic: float,
        epoch: float,
    ) -> None:
        factor = self._concurrency_factor.get(model, 1.0)
        if status_code == 429:
            self._concurrency_factor[model] = max(
                0.05, factor * self.multiplicative_decrease
            )
            retry_after = headers.get("retry-after")
            if retry_after is not None:
                seconds = _retry_after_seconds(retry_after, epoch)
                if seconds is not None:
                    self._cooldown_until[model] = monotonic + seconds
        elif 200 <= status_code < 300:
            self._concurrency_factor[model] = min(1.0, factor + self.additive_step)

    @staticmethod
    def _now(
        now_monotonic: float | None, now_epoch: float | None
    ) -> tuple[float, float]:
        monotonic = now_monotonic if now_monotonic is not None else asyncio.get_running_loop().time()
        epoch = now_epoch if now_epoch is not None else datetime.now(timezone.utc).timestamp()
        if not math.isfinite(monotonic) or not math.isfinite(epoch):
            raise ValueError("clock values must be finite")
        return monotonic, epoch

    @staticmethod
    def _normalize_headers(headers: Mapping[str, str]) -> dict[str, str]:
        return {str(key).lower(): str(value) for key, value in headers.items()}

    @staticmethod
    def _positive_int(value: str | None) -> int | None:
        parsed = AdaptiveLimiter._nonnegative_int(value)
        return parsed if parsed is not None and parsed > 0 else None

    @staticmethod
    def _nonnegative_int(value: str | None) -> int | None:
        try:
            parsed = int(value) if value is not None else None
            return parsed if parsed is not None and parsed >= 0 else None
        except ValueError:
            return None
