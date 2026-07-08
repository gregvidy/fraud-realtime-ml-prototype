"""
app/clickhouse_fallback.py — Slice 10.

Cold-read fallback for the feature service. When Redis is unavailable
(timeout / connection error), scoring falls back to reading a REDUCED
feature set from ClickHouse's `main.stream_latest_features` table.

Design
~~~~~~
* Non-blocking async httpx client against the ClickHouse HTTP interface (:8123).
* Simple circuit breaker: after N consecutive failures, skip the fallback
  and return empty features. Half-open on the next call after `cooldown`
  seconds elapse. On success, reset failure count.
* Feature set is intentionally small: `last_txn_amount`, `last_txn_local_hour`,
  `last_is_international`. The model treats missing features as 0.
* Latency: ~30-50ms end-to-end on a warm CH (P50), vs ~2ms for Redis.
  Acceptable degraded mode — 100× worse than hot path but still << 100ms.

Populated by MVs on `raw.stream_txn_kafka` (see infra/clickhouse/streaming.sql).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Minimal circuit breaker — open after N failures, half-open after cooldown."""

    def __init__(self, fail_threshold: int = 3, cooldown_seconds: float = 5.0) -> None:
        self.fail_threshold = fail_threshold
        self.cooldown = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None

    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        # Half-open after cooldown elapses (one probe allowed)
        return (time.monotonic() - self._opened_at) < self.cooldown

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.fail_threshold:
            self._opened_at = time.monotonic()


class ClickHouseFallback:
    """Async httpx client → CH HTTP interface for cold-fallback feature reads."""

    def __init__(
        self,
        base_url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout_s: float = 2.0,
    ) -> None:
        self._base = base_url or os.getenv("CLICKHOUSE_URL", "http://localhost:8123")
        self._user = user or os.getenv("CLICKHOUSE_USER", "default")
        self._password = password or os.getenv("CLICKHOUSE_PASSWORD", "admin_pass")
        self._timeout = timeout_s
        # httpx AsyncClient reuses connections — one instance per process is fine
        self._client: httpx.AsyncClient | None = None
        self.breaker = CircuitBreaker(fail_threshold=3, cooldown_seconds=5.0)
        self._degraded_count = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                auth=(self._user, self._password),
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_user_features(self, user_id: str) -> tuple[dict[str, float], bool]:
        """Read the latest user features from CH. Returns ({}, False) if unavailable.

        Uses `ORDER BY event_timestamp DESC LIMIT 1` instead of `FINAL` — much
        cheaper on a small key range (order by hits the index directly). FINAL
        would scan the whole ReplacingMergeTree for the entity, which for a
        hot user could be dozens of parts.
        """
        if self.breaker.is_open():
            return {}, False

        query = """
            SELECT features
            FROM main.stream_latest_features
            WHERE entity_type = 'user' AND entity_id = {uid:String}
            ORDER BY event_timestamp DESC
            LIMIT 1
            FORMAT JSONEachRow
        """.strip()

        try:
            client = await self._get_client()
            resp = await client.post(
                "/",
                params={"query": query, "param_uid": user_id},
                content="",
            )
            resp.raise_for_status()
            body = resp.text.strip()
            if not body:
                # Row exists in ORDER BY sense but no data for this user
                self.breaker.record_success()
                return {}, True

            # JSONEachRow returns one JSON object per line; take the first
            row = json.loads(body.splitlines()[0])
            features_map = row.get("features", {})
            # CH Map(String, Float64) → JSON object {feat_name: value}
            features = {k: float(v) for k, v in features_map.items()}

            self.breaker.record_success()
            self._degraded_count += 1
            if self._degraded_count == 1 or self._degraded_count % 100 == 0:
                logger.warning(
                    "clickhouse-fallback: degraded read for user=%s features=%d (cold-read #%d)",
                    user_id, len(features), self._degraded_count,
                )
            return features, True

        except (httpx.HTTPError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            self.breaker.record_failure()
            logger.warning("clickhouse-fallback: query failed — %s (breaker=%s)",
                           exc, "OPEN" if self.breaker.is_open() else "closed")
            return {}, False


# Module-level singleton so httpx pool is reused
_fallback: ClickHouseFallback | None = None


def get_fallback() -> ClickHouseFallback:
    global _fallback
    if _fallback is None:
        _fallback = ClickHouseFallback()
    return _fallback
