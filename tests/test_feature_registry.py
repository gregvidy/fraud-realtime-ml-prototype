"""Tests for the Feature Registry (B1).

Covers:
    1. Load registry from repo's feature_definitions/ succeeds.
    2. Counts: 3 entities, 35 features (3 request + 24 batch + 8 streaming), 1 service.
    3. Validator returns 0 errors on the checked-in registry.
    4. Diff test: fraud_v1 service order matches training/feature_contract.yaml order.
    5. Mode/dtype sanity per group.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fraudml.feature_registry import load_registry
from fraudml.feature_registry.validator import validate


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFS_DIR = REPO_ROOT / "feature_definitions"


@pytest.fixture(scope="module")
def registry():
    return load_registry(DEFS_DIR)


def test_load_succeeds(registry):
    assert registry is not None


def test_counts(registry):
    assert len(registry.entities) == 3, "expected 3 entities (user, device, merchant)"
    assert len(registry.features) == 35, "expected 35 features total"
    assert len(registry.services) == 1, "expected 1 feature_service (fraud_v1)"

    by_mode = {m: 0 for m in ("batch", "streaming", "request")}
    for f in registry.features.values():
        by_mode[f.mode] += 1
    assert by_mode["request"] == 3
    assert by_mode["batch"] == 24
    assert by_mode["streaming"] == 8


def test_validator_clean(registry):
    errors = validate(registry)
    assert errors == [], f"registry has validation errors:\n" + "\n".join(errors)


def test_service_order_matches_feature_contract(registry):
    """The service's feature list MUST match training/feature_contract.yaml order.

    This is the invariant that makes B1 a drop-in replacement for the existing
    contract in Phase C.
    """
    contract_path = REPO_ROOT / "training" / "feature_contract.yaml"
    with open(contract_path) as f:
        contract = yaml.safe_load(f)
    contract_order = [f["name"] for f in contract["features"]]
    service_order = list(registry.services["fraud_v1"].features)
    assert service_order == contract_order


def test_streaming_features_have_valid_redis_ops(registry):
    from fraudml.feature_registry.models import VALID_REDIS_OPS
    streaming = [f for f in registry.features.values() if f.mode == "streaming"]
    assert len(streaming) == 8
    for f in streaming:
        assert f.source["redis_op"] in VALID_REDIS_OPS


def test_batch_features_reference_known_dbt_models(registry):
    allowed = {"fct_user_features", "fct_device_features", "fct_merchant_features"}
    batch = [f for f in registry.features.values() if f.mode == "batch"]
    for f in batch:
        assert f.source["dbt_model"] in allowed, f"unknown dbt_model in {f.name}"


def test_resolve_service_returns_ordered_features(registry):
    features = registry.resolve_service("fraud_v1")
    assert len(features) == 35
    assert features[0].name == "txn_amount"
    assert features[-1].name == "user_failed_logins_15m"
