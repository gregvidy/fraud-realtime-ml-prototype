"""Feature Registry data model — frozen dataclasses matching the YAML schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VALID_MODES = ("batch", "streaming", "request")
VALID_DTYPES = ("int", "float", "bool", "str")
VALID_REDIS_OPS = ("ZCOUNT", "ZRANGEBYSCORE_SUM", "ZCARD_WINDOWED")


@dataclass(frozen=True)
class Entity:
    name: str
    join_key: str
    description: str = ""


@dataclass(frozen=True)
class Feature:
    name: str
    dtype: str
    mode: str                          # batch | streaming | request
    online: bool
    version: int
    entity: str | None                 # None for request features
    source: dict[str, Any]             # mode-specific: see loader.py
    default: Any = 0
    description: str = ""


@dataclass(frozen=True)
class FeatureGroup:
    name: str
    entity: str | None
    version: int
    features: tuple[Feature, ...]
    description: str = ""


@dataclass(frozen=True)
class FeatureService:
    name: str
    version: int
    features: tuple[str, ...]          # ordered feature names
    description: str = ""


@dataclass(frozen=True)
class Registry:
    entities: dict[str, Entity]
    features: dict[str, Feature]        # name → Feature
    groups: dict[str, FeatureGroup]
    services: dict[str, FeatureService]

    def get_entity(self, name: str) -> Entity:
        return self.entities[name]

    def get_feature(self, name: str) -> Feature:
        return self.features[name]

    def get_service(self, name: str) -> FeatureService:
        return self.services[name]

    def resolve_service(self, name: str) -> list[Feature]:
        """Return ordered Feature objects for a feature service."""
        svc = self.get_service(name)
        return [self.features[fname] for fname in svc.features]

    def list_features(self) -> list[str]:
        return list(self.features.keys())

    def list_services(self) -> list[str]:
        return list(self.services.keys())
