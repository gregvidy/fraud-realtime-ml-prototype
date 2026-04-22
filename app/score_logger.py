"""
score_logger.py
---------------
Async queue-based score logger.

log_score() is a non-blocking fire-and-forget call (~0 µs) that enqueues
the payload into an in-process asyncio.Queue.  A background drain task
batch-inserts rows into Postgres via asyncpg every 50 ms or 100 rows,
whichever comes first.

Workflow:
    1. main.py startup calls ``await score_logger.init(pool)``
    2. Every ``score_transaction()`` call invokes ``log_score(...)`` — zero blocking
    3. Background drain task flushes to DB asynchronously
"""

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)

_queue: asyncio.Queue | None = None
_pool: asyncpg.Pool | None = None
_drain_task: asyncio.Task | None = None

_INSERT = """
INSERT INTO model_score_log
    (transaction_id, user_id, device_id, merchant_id,
     fraud_score, risk_band, is_flagged, model_version,
     feature_service_version, feast_offline_ok, redis_online_ok)
VALUES
    ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT DO NOTHING
"""


async def init(pool: asyncpg.Pool) -> None:
    """Initialise pool and start the background drain task. Called once at startup."""
    global _queue, _pool, _drain_task
    _pool = pool
    _queue = asyncio.Queue()
    _drain_task = asyncio.create_task(_drain_loop())
    logger.info("score_logger: async drain task started")


async def shutdown() -> None:
    """Cancel the drain task gracefully at app shutdown."""
    global _drain_task
    if _drain_task:
        _drain_task.cancel()
        try:
            await _drain_task
        except asyncio.CancelledError:
            pass


def log_score(
    transaction_id: str,
    user_id: str,
    device_id: str,
    merchant_id: str,
    fraud_score: float,
    risk_band: str,
    is_flagged: bool,
    model_version: str,
    feature_service_version: str,
    feast_offline_ok: bool,
    redis_online_ok: bool,
) -> None:
    """Non-blocking enqueue — safe to call from async context without await."""
    if _queue is None:
        return
    try:
        _queue.put_nowait((
            transaction_id, user_id, device_id, merchant_id,
            fraud_score, risk_band, is_flagged, model_version,
            feature_service_version, feast_offline_ok, redis_online_ok,
        ))
    except asyncio.QueueFull:
        logger.warning("score_logger: queue full, dropping log for %s", transaction_id)


# ---------------------------------------------------------------------------
# Internal drain loop
# ---------------------------------------------------------------------------

async def _flush(batch: list, pool: asyncpg.Pool) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.executemany(_INSERT, batch)
    except Exception as exc:
        logger.warning("score_logger: batch insert failed (%d rows) — %s", len(batch), exc)


async def _drain_loop() -> None:
    """Drain the queue and batch-insert to Postgres every 50 ms or 100 rows."""
    while True:
        batch: list = []
        try:
            # Block up to 50 ms waiting for the first item
            item = await asyncio.wait_for(_queue.get(), timeout=0.05)
            batch.append(item)
            # Drain any already-queued items without blocking (up to 100 total)
            while len(batch) < 100:
                try:
                    batch.append(_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
        except asyncio.TimeoutError:
            pass

        if batch and _pool is not None:
            await _flush(batch, _pool)
