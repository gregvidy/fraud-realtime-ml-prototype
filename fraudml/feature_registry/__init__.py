"""Feature Registry — B1.

Loads YAML feature definitions from `feature_definitions/` and exposes them
as strongly-typed Python objects for training and serving code to consume.

Public API:
    load_registry(root: Path) -> Registry
    Registry.get_feature(name) -> Feature
    Registry.get_service(name) -> FeatureService
    Registry.resolve_service(name) -> list[Feature]     # ordered feature vector
    Registry.validate() -> list[str]                    # syntactic errors
"""

from .models import Entity, Feature, FeatureGroup, FeatureService, Registry
from .loader import load_registry

__all__ = [
    "Entity",
    "Feature",
    "FeatureGroup",
    "FeatureService",
    "Registry",
    "load_registry",
]
