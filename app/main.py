"""
app/main.py — FastAPI scoring service for fraud detection.
"""

import logging
import os

import asyncpg
import redis as redis_lib
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import feature_logger, score_logger
from .model_loader import load_model, get_model
from .schemas import HealthResponse, ScoreRequest, ScoreResponse
from .scoring import score_transaction

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fraud Detection Scoring API",
    description="Real-time fraud score endpoint backed by XGBoost + Feast + Redis.",
    version="1.0.0",
)


@app.on_event("startup")
async def startup_event():
    # --- Async DB logging pool ---
    try:
        pool = await asyncpg.create_pool(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            user=os.getenv("POSTGRES_USER", "fraud_user"),
            password=os.getenv("POSTGRES_PASSWORD", "fraud_pass"),
            database=os.getenv("POSTGRES_DB", "fraud_db"),
            min_size=5,
            max_size=20,
            command_timeout=5,
        )
        await score_logger.init(pool)
        await feature_logger.init(pool)
        logger.info("DB logging pool initialized (asyncpg, min=5 max=20).")
    except Exception as exc:
        logger.warning("DB pool init failed — score/feature logging disabled: %s", exc)

    # --- ML model ---
    try:
        load_model()
        logger.info("Model loaded successfully at startup.")
    except FileNotFoundError as exc:
        logger.warning("Startup warning: %s", exc)


@app.on_event("shutdown")
async def shutdown_event():
    await score_logger.shutdown()
    await feature_logger.shutdown()


@app.post("/score", response_model=ScoreResponse, summary="Score a transaction for fraud")
async def score_endpoint(request: ScoreRequest):
    """
    Score a payment transaction for fraud probability.

    - Fetches offline features from Feast (materialized from dbt).
    - Fetches online features from Redis (sliding-window counters).
    - Combines with request-time features and runs the XGBoost model.
    """
    if get_model() is None:
        raise HTTPException(
            status_code=503,
            detail="Model not loaded. Run 'make train' and restart the API."
        )
    try:
        return await score_transaction(request)
    except Exception as exc:
        logger.exception("Scoring error for transaction %s", request.transaction_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health", response_model=HealthResponse, summary="Health check")
def health_check():
    model_loaded = get_model() is not None

    redis_ok = False
    try:
        r = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
        )
        r.ping()
        redis_ok = True
    except Exception:
        pass

    return HealthResponse(
        status="ok" if (model_loaded and redis_ok) else "degraded",
        model_loaded=model_loaded,
        redis_connected=redis_ok,
    )


@app.get("/", include_in_schema=False)
def root():
    return JSONResponse({"message": "Fraud Detection API — see /docs"})
