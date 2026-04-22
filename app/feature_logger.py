"""
feature_logger.py
-----------------
Async queue-based online feature logger.

Mirrors score_logger.py: log_online_features() is a non-blocking put_nowait().
A background drain task batch-inserts via asyncpg every 50 ms or 100 rows.

At training time, build_training_dataset.py --use-feature-log joins this
table by transaction_id to replace dbt-derived online features with the
values the model actually saw, eliminating training-serving skew.
"""

import asyncio
import logging

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Online feature column names — must stay in sync with retriever.py output
# ---------------------------------------------------------------------------
ONLINE_FEATURE_COLS: tuple[str, ...] = (
    "user_txn_count_5m",
    "user_txn_count_10m",
    "user_txn_count_1h",
    "user_txn_amount_sum_5m",
    "user_txn_amount_sum_10m",
    "user_txn_amount_sum_1h",
    "user_distinct_merchants_5m",
    "user_distinct_merchants_10m",
    "user_distinct_merchants_1h",
    "user_failed_logins_15m",
    "user_failed_logins_1h",
    "device_txn_count_5m",
    "device_txn_count_10m",
    "device_txn_count_1h",
)

_queue: asyncio.Queue | None = None
_pool: asyncpg.Pool | None = None
_drain_task: asyncio.Task | None = None

_INSERT = """
INSERT INTO online_feature_log (
    transaction_id, user_id, device_id,
    user_txn_count_5m, user_txn_count_10m, user_txn_count_1h,
    user_txn_amount_sum_5m, user_txn_amount_sum_10m, user_txn_amount_sum_1h,
    user_distinct_merchants_5m, user_distinct_merchants_10m, user_distinct_merchants_1h,
    user_failed_logins_15m, user_failed_logins_1h,
    device_txn_count_5m, device_txn_count_10m, device_txn_count_1h
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
)
ON CONFLICT DO NOTHING
"""


async def init(pool: asyncpg.Pool) -> None:
    """Initialise pool and start the background drain task. Called once at startup."""
    global _queue, _pool, _drain_task
    _pool = pool
    _queue = asyncio.Queue()
    _drain_task = asyncio.create_task(_drain_loop())
    logger.info("feature_logger: async drain task started")


async def shutdown() -> None:
    """Cancel the drain task gracefully at app shutdown."""
    global _drain_task
    if _drain_task:
        _drain_task.cancel()
        try:
            await _drain_task
        except asyncio.CancelledError:
            pass


def log_online_features(
    transaction_id: str,
    user_id: str,
    device_id: str,
    online_features: dict,
) -> None:
    """
    Non-blocking enqueue. Safe to call from async context without await.

    Parameters
    ----------
    transaction_id  : The transaction being scored.
    user_id         : Entity ID for user.
    device_id       : Entity ID for device.
    online_features : The dict returned by retriever.get_all_online_features().
                      Missing keys default to 0.
    """
    if _queue is None:
        return
    row = (
        transaction_id,
        user_id,
        device_id,
        *(online_features.get(col) or 0 for col in ONLINE_FEATURE_COLS),
    )
    try:
        _queue.put_nowait(row)
    except asyncio.QueueFull:
        logger.warning("feature_logger: queue full, dropping log for %s", transaction_id)


# ---------------------------------------------------------------------------
# Internal drain loop
# ---------------------------------------------------------------------------

async def _flush(batch: list, pool: asyncpg.Pool) -> None:
    try:
        async with pool.acquire() as conn:
            await conn.executemany(_INSERT, batch)
    except Exception as exc:
        logger.warning("feature_logger: batch insert failed (%d rows) — %s", len(batch), exc)


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
