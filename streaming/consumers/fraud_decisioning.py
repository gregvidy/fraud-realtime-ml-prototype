"""
streaming/consumers/fraud_decisioning.py
----------------------------------------
Reads txn.raw.<channel> events and:
  1. POSTs each event to the FastAPI scoring service (`/score`) over HTTP with
     connection keep-alive (httpx.AsyncClient).
  2. Publishes the score result as a ScoredTxnEvent to `txn.scored` (Avro).

HTTP concurrency is bounded by a semaphore so a large consumer batch doesn't
open hundreds of simultaneous connections to the scoring API. Failed scores
are logged and NOT written to txn.scored (they still commit — the API will
reprocess on the next producer run if the transaction gets replayed).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from streaming.config import (  # noqa: E402
    GROUP_FRAUD_DECISIONING,
    REDPANDA_BROKER,
    SCHEMA_REGISTRY_URL,
    SCORED_TXN_EVENT_SCHEMA_PATH,
    TXN_EVENT_SCHEMA_PATH,
    TXN_SCORED_TOPIC,
)
from streaming.consumers.base import AsyncAvroConsumer, DecodedRecord  # noqa: E402
from streaming.producer import AvroTxnProducer  # noqa: E402

load_dotenv()

TOPIC_PATTERN = r"^txn\.raw\..+$"

SCORING_URL = os.getenv("SCORING_URL", "http://localhost:8000/score")
HTTP_CONCURRENCY = int(os.getenv("SCORING_HTTP_CONCURRENCY", "32"))
HTTP_TIMEOUT_S = float(os.getenv("SCORING_HTTP_TIMEOUT", "5.0"))


def _txn_to_score_request(v: dict) -> dict:
    """Build the JSON body /score expects. Uses only the request-time fields
    the API needs — the scoring service pulls user/device/merchant features
    from Redis itself."""
    return {
        "transaction_id":   v["transaction_id"],
        "user_id":          v["user_id"],
        "device_id":        v["device_id"],
        "merchant_id":      v["merchant_id"],
        "amount":           v["amount"],
        "currency":         v["currency"],
        "payment_method":   v["payment_method"],
        "country_code":     v["country_code"],
        "is_international": bool(v["is_international"]),
        "local_hour":       int(v["local_hour"]),
    }


def _build_scored_event(source: dict, score_response: dict) -> dict:
    """Map API response + source txn into the ScoredTxnEvent shape."""
    return {
        "transaction_id":         source["transaction_id"],
        "user_id":                source["user_id"],
        "device_id":              source["device_id"],
        "merchant_id":            source["merchant_id"],
        "channel":                source["channel"],
        "amount":                 source["amount"],
        "is_international":       bool(source["is_international"]),
        "event_timestamp":        source["event_timestamp"],
        "fraud_score":            float(score_response["score"]),
        "risk_band":              score_response["risk_band"],
        "is_flagged":             bool(score_response["is_flagged"]),
        "model_version":          score_response.get("model_version", "unknown"),
        "feature_service_version": score_response.get(
            "feature_service_version", "fraud_scoring_v1"
        ),
        "scored_at":              datetime.now(timezone.utc),
    }


class FraudDecisioning:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None
        self._producer: AvroTxnProducer | None = None
        self._sem = asyncio.Semaphore(HTTP_CONCURRENCY)
        self.n_scored = 0
        self.n_flagged = 0
        self.n_http_errors = 0

    async def start(self) -> None:
        limits = httpx.Limits(
            max_connections=HTTP_CONCURRENCY,
            max_keepalive_connections=HTTP_CONCURRENCY,
        )
        self._http = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, limits=limits)
        self._producer = AvroTxnProducer(
            bootstrap_servers=REDPANDA_BROKER,
            schema_registry_url=SCHEMA_REGISTRY_URL,
            value_schema_path=SCORED_TXN_EVENT_SCHEMA_PATH,
            client_id="fraud-decisioning-producer",
        )

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
        if self._producer is not None:
            self._producer.flush(timeout=15)

    async def _score_one(self, rec: DecodedRecord) -> None:
        assert self._http is not None and self._producer is not None
        req_body = _txn_to_score_request(rec.value)
        try:
            async with self._sem:
                resp = await self._http.post(SCORING_URL, json=req_body)
                resp.raise_for_status()
                score_response = resp.json()
        except (httpx.HTTPError, httpx.RequestError) as exc:
            self.n_http_errors += 1
            if self.n_http_errors <= 5:  # avoid log spam
                print(f"[fraud_decisioning] scoring error for {rec.value['transaction_id']}: {exc}")
            return

        scored = _build_scored_event(rec.value, score_response)
        self._producer.publish(TXN_SCORED_TOPIC, key=rec.value["user_id"], value=scored)
        self.n_scored += 1
        if scored["is_flagged"]:
            self.n_flagged += 1

    async def handle(self, batch: list[DecodedRecord]) -> None:
        # Fan out concurrent /score requests. Sem bounds actual concurrency.
        await asyncio.gather(*(self._score_one(r) for r in batch))
        if self.n_scored and self.n_scored % 1000 < len(batch):
            print(f"[fraud_decisioning] scored={self.n_scored} "
                  f"flagged={self.n_flagged} http_errors={self.n_http_errors}")


async def run(*, duration: float = 0.0) -> None:
    svc = FraudDecisioning()
    await svc.start()
    try:
        consumer = AsyncAvroConsumer(
            name="fraud_decisioning",
            group_id=GROUP_FRAUD_DECISIONING,
            bootstrap_servers=REDPANDA_BROKER,
            schema_registry_url=SCHEMA_REGISTRY_URL,
            value_schema_path=TXN_EVENT_SCHEMA_PATH,
            topic_pattern=TOPIC_PATTERN,
            batch_size=200,        # smaller so per-batch HTTP fan-out is bounded
            batch_timeout_ms=250,
        )
        await consumer.run(svc.handle, duration=duration)
    finally:
        await svc.stop()
        print(f"[fraud_decisioning] SUMMARY scored={svc.n_scored} "
              f"flagged={svc.n_flagged} http_errors={svc.n_http_errors}")


if __name__ == "__main__":
    asyncio.run(run())
