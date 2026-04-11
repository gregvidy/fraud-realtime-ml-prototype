"""
entities.py — Feast entity definitions for the fraud detection MVP.
"""

from feast import Entity, ValueType

# ---------------------------------------------------------------------------
# User entity
# ---------------------------------------------------------------------------
user = Entity(
    name="user",
    join_keys=["user_id"],
    value_type=ValueType.STRING,
    description="Registered user of the payment platform.",
)

# ---------------------------------------------------------------------------
# Device entity
# ---------------------------------------------------------------------------
device = Entity(
    name="device",
    join_keys=["device_id"],
    value_type=ValueType.STRING,
    description="Device used to initiate transactions.",
)

# ---------------------------------------------------------------------------
# Merchant entity
# ---------------------------------------------------------------------------
merchant = Entity(
    name="merchant",
    join_keys=["merchant_id"],
    value_type=ValueType.STRING,
    description="Merchant receiving the payment.",
)
