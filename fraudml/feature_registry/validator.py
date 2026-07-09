"""Syntactic validator for the Feature Registry.

Runs on a loaded Registry object (structural errors are already surfaced by
loader.load_registry). Checks semantic invariants:

  1. Every feature referenced in a feature_service exists.
  2. Every non-request feature's entity exists.
  3. Feature mode ⇔ source fields are consistent:
       batch     → source has {dbt_model, column}
       streaming → source has {redis_key_template, redis_op, window}
       request   → source has {json_path}
  4. Streaming redis_op is one of the supported ops.
  5. Streaming redis_key_template contains the entity's join_key placeholder.

Returns a list of error messages. Empty list = valid.

Does NOT connect to external systems (dbt, ClickHouse, Redis, Redpanda). That's
Phase F concern (data quality + drift + external freshness).
"""

from __future__ import annotations

from .models import Feature, Registry, VALID_REDIS_OPS


def _check_source(feat: Feature, entity_join_key: str | None) -> list[str]:
    src = feat.source or {}
    errors: list[str] = []

    if feat.mode == "batch":
        for k in ("dbt_model", "column"):
            if k not in src:
                errors.append(
                    f"feature {feat.name!r}: mode=batch requires source.{k}"
                )
    elif feat.mode == "streaming":
        for k in ("redis_key_template", "redis_op", "window"):
            if k not in src:
                errors.append(
                    f"feature {feat.name!r}: mode=streaming requires source.{k}"
                )
        if src.get("redis_op") and src["redis_op"] not in VALID_REDIS_OPS:
            errors.append(
                f"feature {feat.name!r}: redis_op={src['redis_op']!r} not in {VALID_REDIS_OPS}"
            )
        # entity join_key must appear as a placeholder in the key template
        tpl = src.get("redis_key_template", "")
        if entity_join_key and f"{{{entity_join_key}}}" not in tpl:
            errors.append(
                f"feature {feat.name!r}: redis_key_template must contain "
                f"{{{entity_join_key}}} placeholder, got {tpl!r}"
            )
    elif feat.mode == "request":
        if "json_path" not in src:
            errors.append(
                f"feature {feat.name!r}: mode=request requires source.json_path"
            )

    return errors


def validate(registry: Registry) -> list[str]:
    errors: list[str] = []

    # 1. entity refs
    for feat in registry.features.values():
        if feat.entity is not None and feat.entity not in registry.entities:
            errors.append(
                f"feature {feat.name!r}: unknown entity {feat.entity!r}"
            )

    # 2. mode → source consistency
    for feat in registry.features.values():
        ent_key = (
            registry.entities[feat.entity].join_key
            if feat.entity and feat.entity in registry.entities
            else None
        )
        errors.extend(_check_source(feat, ent_key))

    # 3. service feature refs
    for svc in registry.services.values():
        for fname in svc.features:
            if fname not in registry.features:
                errors.append(
                    f"feature_service {svc.name!r}: unknown feature {fname!r}"
                )

    return errors
