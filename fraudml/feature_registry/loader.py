"""YAML loader for the Feature Registry.

Reads all `*.yaml` files under a `feature_definitions/` directory and returns
a fully-populated Registry. Fails fast on structural errors (missing keys,
duplicate names). Semantic validation lives in validator.validate().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    Entity,
    Feature,
    FeatureGroup,
    FeatureService,
    Registry,
    VALID_DTYPES,
    VALID_MODES,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _parse_feature(raw: dict[str, Any], group_entity: str | None) -> Feature:
    for req in ("name", "dtype", "mode"):
        if req not in raw:
            raise ValueError(f"feature missing required key '{req}': {raw!r}")

    if raw["mode"] not in VALID_MODES:
        raise ValueError(
            f"feature {raw['name']!r}: mode must be one of {VALID_MODES}, got {raw['mode']!r}"
        )
    if raw["dtype"] not in VALID_DTYPES:
        raise ValueError(
            f"feature {raw['name']!r}: dtype must be one of {VALID_DTYPES}, got {raw['dtype']!r}"
        )

    return Feature(
        name=raw["name"],
        dtype=raw["dtype"],
        mode=raw["mode"],
        online=bool(raw.get("online", False)),
        version=int(raw.get("version", 1)),
        entity=None if raw["mode"] == "request" else group_entity,
        source=raw.get("source", {}) or {},
        default=raw.get("default", 0),
        description=raw.get("description", ""),
    )


def _parse_feature_group(doc: dict[str, Any]) -> FeatureGroup:
    if "feature_group" not in doc:
        raise ValueError("feature-group YAML missing top-level 'feature_group' key")
    fg = doc["feature_group"]
    entity = fg.get("entity")
    feats = tuple(_parse_feature(f, entity) for f in doc.get("features", []))
    return FeatureGroup(
        name=fg["name"],
        entity=entity,
        version=int(fg.get("version", 1)),
        features=feats,
        description=fg.get("description", ""),
    )


def _parse_entities(doc: dict[str, Any]) -> dict[str, Entity]:
    out: dict[str, Entity] = {}
    for e in doc.get("entities", []):
        if "name" not in e or "join_key" not in e:
            raise ValueError(f"entity missing required keys: {e!r}")
        if e["name"] in out:
            raise ValueError(f"duplicate entity name: {e['name']!r}")
        out[e["name"]] = Entity(
            name=e["name"],
            join_key=e["join_key"],
            description=e.get("description", ""),
        )
    return out


def _parse_services(doc: dict[str, Any]) -> dict[str, FeatureService]:
    out: dict[str, FeatureService] = {}
    for s in doc.get("feature_services", []):
        if "name" not in s or "features" not in s:
            raise ValueError(f"feature_service missing required keys: {s!r}")
        if s["name"] in out:
            raise ValueError(f"duplicate feature_service name: {s['name']!r}")
        out[s["name"]] = FeatureService(
            name=s["name"],
            version=int(s.get("version", 1)),
            features=tuple(s["features"]),
            description=s.get("description", ""),
        )
    return out


def load_registry(root: Path | str) -> Registry:
    """Load a Registry from a directory of YAML files.

    Expected files (any subset OK, but usually all present):
        entities.yaml           top-level 'entities:' list
        <group>_features.yaml   top-level 'feature_group:' + 'features:' list
        services.yaml           top-level 'feature_services:' list

    Fails fast on structural errors. Semantic checks (dangling refs,
    mode-specific source fields) live in validator.validate().
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"feature_definitions directory not found: {root}")

    entities: dict[str, Entity] = {}
    features: dict[str, Feature] = {}
    groups: dict[str, FeatureGroup] = {}
    services: dict[str, FeatureService] = {}

    for path in sorted(root.glob("*.yaml")):
        doc = _load_yaml(path)
        if "entities" in doc:
            for name, ent in _parse_entities(doc).items():
                if name in entities:
                    raise ValueError(f"duplicate entity {name!r} across files")
                entities[name] = ent
        elif "feature_group" in doc:
            fg = _parse_feature_group(doc)
            if fg.name in groups:
                raise ValueError(f"duplicate feature_group {fg.name!r} across files")
            groups[fg.name] = fg
            for feat in fg.features:
                if feat.name in features:
                    raise ValueError(f"duplicate feature {feat.name!r} across files")
                features[feat.name] = feat
        elif "feature_services" in doc:
            for name, svc in _parse_services(doc).items():
                if name in services:
                    raise ValueError(f"duplicate feature_service {name!r} across files")
                services[name] = svc

    return Registry(
        entities=entities, features=features, groups=groups, services=services
    )
