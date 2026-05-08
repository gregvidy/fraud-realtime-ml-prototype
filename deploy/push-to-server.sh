#!/usr/bin/env bash
# ==============================================================================
# push-to-server.sh — Copy project + start services on remote EC2 instance
#
# Usage:
#   ./deploy/push-to-server.sh <PUBLIC_IP>
#   ./deploy/push-to-server.sh              # reads from .instance-info
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load instance info
if [ -f "$SCRIPT_DIR/.instance-info" ]; then
    source "$SCRIPT_DIR/.instance-info"
fi

HOST="${1:-${PUBLIC_IP:-}}"
KEY="${KEY_NAME:-fraud-demo-key}"

if [ -z "$HOST" ]; then
    echo "Usage: $0 <PUBLIC_IP>"
    echo "   or: run deploy-aws.sh first to create .instance-info"
    exit 1
fi

SSH_OPTS="-i ${KEY}.pem -o StrictHostKeyChecking=no -o ConnectTimeout=10"
SSH="ssh $SSH_OPTS ubuntu@$HOST"
SCP="scp $SSH_OPTS"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying to $HOST                                         "
echo "╚══════════════════════════════════════════════════════════════╝"

# ── Wait for cloud-init to finish ─────────────────────────────────────────────
echo "→ Waiting for server to be ready..."
for i in $(seq 1 30); do
    if $SSH "cloud-init status --wait 2>/dev/null | grep -q done" 2>/dev/null; then
        break
    fi
    echo "  Attempt $i/30 — waiting..."
    sleep 10
done

# ── Copy project files ────────────────────────────────────────────────────────
echo "→ Syncing project files..."
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'node_modules' \
    --exclude 'mlruns' \
    --exclude 'mlflow.db' \
    --exclude 'logs/' \
    --exclude 'training/datasets/' \
    --exclude 'data/duckdb/' \
    --exclude '.env.local' \
    "$PROJECT_DIR/" "ubuntu@$HOST:/opt/fraud-demo/"

# ── Start services on remote ──────────────────────────────────────────────────
echo "→ Starting services..."
$SSH << 'REMOTE_SCRIPT'
set -ex
cd /opt/fraud-demo

# Build and start
docker compose -f deploy/docker-compose.prod.yml up -d --build

# Wait for health checks
echo "Waiting for services to be healthy..."
sleep 10

# Verify
echo ""
echo "=== Service Status ==="
docker compose -f deploy/docker-compose.prod.yml ps
echo ""
echo "=== Health Check ==="
curl -s http://localhost:8000/health | python3 -m json.tool || echo "API not ready yet — try again in 30s"
echo ""
echo "=== Quick Scoring Test ==="
curl -s -X POST http://localhost:8000/score \
    -H "Content-Type: application/json" \
    -d '{"transaction_id":"deploy-test","user_id":"u_000001","device_id":"d_0000001","merchant_id":"m_00001","amount":250.00,"is_international":false}' \
    | python3 -m json.tool || echo "Score endpoint not ready yet"
REMOTE_SCRIPT

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓ Deployment complete!                                     ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              "
echo "║  API:  http://$HOST:8000/score                              "
echo "║  Health: http://$HOST:8000/health                           "
echo "║                                                              "
echo "║  Load test from your laptop:                                 ║"
echo "║    make load-test API_HOST=http://$HOST:8000                "
echo "║    make load-test API_HOST=http://$HOST:8000 USERS=1000     "
echo "║                                                              "
echo "║  SSH:  ssh -i ${KEY}.pem ubuntu@$HOST                      "
echo "╚══════════════════════════════════════════════════════════════╝"
