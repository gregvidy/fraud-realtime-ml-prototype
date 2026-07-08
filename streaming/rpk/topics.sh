#!/usr/bin/env bash
# ============================================================================
# streaming/rpk/topics.sh
# ---------------------------------------------------------------------------
# Creates the per-channel raw topics + txn.scored + login.events on Redpanda.
# Idempotent — --create is safe to re-run because we `rpk topic create` with
# alter-conflict handled by re-listing at the end.
#
# Topic specs are declared in one place (below); if they diverge from
# streaming/config.py, config.py is the source of truth.
# ============================================================================
set -eu

CONTAINER="${REDPANDA_CONTAINER:-fraud_redpanda}"

# name : partitions : retention_hours
TOPICS=(
    "txn.raw.visa       :6:168"
    "txn.raw.mastercard :6:168"
    "txn.raw.amex       :3:168"
    "txn.raw.qris       :6:168"
    "txn.raw.debit      :3:168"
    "txn.raw.digital    :6:168"
    "txn.scored         :12:720"
    "login.events       :6:168"
)

rpk() {
    docker exec "$CONTAINER" rpk "$@"
}

echo "── creating topics on $CONTAINER ──"
for spec in "${TOPICS[@]}"; do
    IFS=':' read -r name parts hours <<< "$spec"
    name="$(echo "$name" | tr -d '[:space:]')"
    retention_ms=$(( hours * 3600 * 1000 ))
    if rpk topic create "$name" \
        --partitions "$parts" \
        --replicas 1 \
        --config "retention.ms=$retention_ms" \
        --config "cleanup.policy=delete" \
        > /dev/null 2>&1; then
        echo "  created  $name  (partitions=$parts, retention=${hours}h)"
    else
        # Already exists — align retention.ms in case it changed.
        rpk topic alter-config "$name" --set "retention.ms=$retention_ms" > /dev/null 2>&1 || true
        echo "  exists   $name  (partitions=$parts, retention=${hours}h)"
    fi
done

echo ""
echo "── rpk topic list ──"
rpk topic list
