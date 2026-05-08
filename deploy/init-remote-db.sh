#!/usr/bin/env bash
# ==============================================================================
# init-remote-db.sh — One-time: seed Postgres on EC2 with reference data
#
# Run ONCE after first deploy to populate raw_users, raw_devices, raw_merchants,
# and raw_transactions so the simulator can stream events.
#
# Usage:
#   ./deploy/init-remote-db.sh
#   make deploy-init     # Makefile target
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.instance-info" ] && source "$SCRIPT_DIR/.instance-info"

INSTANCE_ID="${1:-${INSTANCE_ID:-}}"
REGION="${REGION:-us-east-1}"

if [ -z "$INSTANCE_ID" ]; then
    echo "ERROR: No instance ID. Run: make deploy-aws first."
    exit 1
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Seeding EC2 Postgres (one-time setup)                      ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Instance: $INSTANCE_ID"
echo "║  Region:   $REGION"
echo "║                                                              "
echo "║  This generates:                                             "
echo "║    - 4000 users, 500 merchants, ~6500 devices                "
echo "║    - ~580K+ transactions (2025-10-01 → 2026-03-31)           "
echo "║    - Fraud rate: 0.5% – 1.0%                                 "
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

PARAMS_FILE=$(mktemp /tmp/ssm-init-XXXX.json)

python3 > "$PARAMS_FILE" << 'PYEOF'
import json

commands = [
    "set -ex",
    "cd /home/ubuntu/fraud-realtime-ml-prototype",
    "docker compose -f deploy/docker-compose.prod.yml --profile training run --rm -T training sh -c '"
    "python simulator/generate_reference_data.py --n-users 4000 --n-merchants 500 && "
    "python simulator/generate_historical_transactions.py "
    "--start-date 2025-10-01 --end-date 2026-03-31 "
    "--fraud-rate-min 0.005 --fraud-rate-max 0.01"
    "'",
    "echo '=== DB seeding complete ==='",
]

print(json.dumps({
    "commands": commands,
    "executionTimeout": ["1800"]
}))
PYEOF

COMMAND_ID=$(aws --no-cli-pager ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters "file://$PARAMS_FILE" \
    --comment "fraud-ml init-db" \
    --query "Command.CommandId" --output text)
rm -f "$PARAMS_FILE"

echo "  Command ID: $COMMAND_ID"
echo "→ Waiting for DB seeding (~2-5 min for 580K rows)..."

for i in $(seq 1 60); do
    STATUS=$(aws --no-cli-pager ssm get-command-invocation \
        --region "$REGION" \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" \
        --query "Status" --output text 2>/dev/null || echo "Pending")

    if [ "$STATUS" = "Success" ]; then
        echo ""
        echo "  ✓ DB seeding complete!"
        OUTPUT=$(aws --no-cli-pager ssm get-command-invocation \
            --region "$REGION" \
            --command-id "$COMMAND_ID" \
            --instance-id "$INSTANCE_ID" \
            --query "StandardOutputContent" --output text 2>/dev/null | tail -20)
        echo "$OUTPUT"
        echo ""
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║  Postgres seeded. You can now run:                          ║"
        echo "║    make stream-docker EPS=20   (via ssm-shell)              ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        exit 0
    elif [ "$STATUS" = "Failed" ] || [ "$STATUS" = "TimedOut" ] || [ "$STATUS" = "Cancelled" ]; then
        echo ""
        echo "  ✗ DB seeding failed ($STATUS)"
        aws --no-cli-pager ssm get-command-invocation \
            --region "$REGION" \
            --command-id "$COMMAND_ID" \
            --instance-id "$INSTANCE_ID" \
            --query "[StandardOutputContent, StandardErrorContent]" --output text | tail -30
        exit 1
    fi

    printf "\r  [%2d/60] %s   — waiting 10s..." "$i" "$STATUS"
    sleep 10
done

echo ""
echo "  ⚠ Timed out (10 min). Check manually:"
echo "    aws ssm get-command-invocation --command-id $COMMAND_ID --instance-id $INSTANCE_ID --region $REGION"
exit 1
