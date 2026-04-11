"""
schemas.py — Pydantic request/response schemas for the scoring API.
"""

from typing import Optional

from pydantic import BaseModel, Field


class ScoreRequest(BaseModel):
    transaction_id: str = Field(..., description="Unique transaction identifier")
    user_id:        str = Field(..., description="User entity key")
    device_id:      str = Field(..., description="Device entity key")
    merchant_id:    str = Field(..., description="Merchant entity key")
    amount:         float = Field(..., gt=0, description="Transaction amount (positive)")
    currency:       str = Field(default="USD", description="ISO 4217 currency code")
    payment_method: str = Field(default="card")
    country_code:   str = Field(default="US", description="ISO 3166-1 alpha-2")
    is_international: bool = Field(default=False)
    local_hour:     Optional[int] = Field(default=None, ge=0, le=23)

    model_config = {
        "json_schema_extra": {
            "example": {
                "transaction_id": "txn-abc123",
                "user_id": "u_000001",
                "device_id": "d_0000001",
                "merchant_id": "m_00001",
                "amount": 350.00,
                "currency": "USD",
                "payment_method": "card",
                "country_code": "US",
                "is_international": False,
                "local_hour": 14,
            }
        }
    }


class ScoreResponse(BaseModel):
    transaction_id: str
    score:          float = Field(..., description="Fraud probability score [0, 1]")
    risk_band:      str   = Field(..., description="low | medium | high | critical")
    is_flagged:     bool  = Field(..., description="True if score >= decision threshold")
    model_version:  str
    feature_sources: dict = Field(
        default_factory=dict,
        description="Summary of feature source availability"
    )


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    redis_connected: bool
