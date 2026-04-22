"""
locustfile.py
-------------
Locust load test for the fraud scoring API.

Quick-start (300 TPS target):
    make load-test                    # headless, 300 users, ramp 30/s → ~300 TPS
    make load-test USERS=150 RATE=15  # lighter run
    make load-test-ui                 # open browser UI on http://localhost:8089

The synthetic user pool mirrors generate_reference_data.py defaults:
    2 000 users  (u_000001 … u_002000)
    4 000 devices (d_0000001 … d_0004000)
      300 merchants (m_00001 … m_00300)
"""

import random
import uuid

from locust import HttpUser, constant_throughput, task


# ---------------------------------------------------------------------------
# Entity pools — match generate_reference_data.py defaults
# ---------------------------------------------------------------------------
_N_USERS     = 2_000
_N_DEVICES   = 4_000
_N_MERCHANTS = 300


def _user_id() -> str:
    return f"u_{random.randint(1, _N_USERS):06d}"


def _device_id() -> str:
    return f"d_{random.randint(1, _N_DEVICES):07d}"


def _merchant_id() -> str:
    return f"m_{random.randint(1, _N_MERCHANTS):05d}"


# ---------------------------------------------------------------------------
# Locust user
# ---------------------------------------------------------------------------

class FraudScoringUser(HttpUser):
    """
    Each simulated user fires POST /score as fast as it can (no think time).
    At 300 concurrent users the API should sustain ~300 TPS assuming ~1 s
    latency budget per user. With <100 ms latency target, each user can
    issue ~10 req/s, so 300 users → ~3 000 req/s headroom; ramp slowly.

    Use constant_throughput(1) to target exactly 1 req/s per user
    (= total TPS ≈ concurrency).  Set USERS=300 → ~300 TPS.
    """
    wait_time = constant_throughput(1)  # 1 request/second per simulated user

    @task
    def score(self):
        payload = {
            "transaction_id": str(uuid.uuid4()),
            "user_id":        _user_id(),
            "device_id":      _device_id(),
            "merchant_id":    _merchant_id(),
            "amount":         round(random.uniform(5.0, 2_000.0), 2),
            "currency":       "USD",
            "payment_method": random.choice(["card", "wallet", "bank_transfer"]),
            "country_code":   random.choice(["US", "GB", "SG", "MY"]),
            "is_international": random.random() < 0.15,
            "local_hour":     random.randint(0, 23),
        }
        with self.client.post(
            "/score",
            json=payload,
            catch_response=True,
            name="/score",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 503:
                resp.failure("Model not loaded")
            else:
                resp.failure(f"HTTP {resp.status_code}")
