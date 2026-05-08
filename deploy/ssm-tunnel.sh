#!/usr/bin/env bash
# ==============================================================================
# ssm-tunnel.sh — Port-forward EC2 services to localhost via SSM (no SSH)
#
# Tunnels over port 443 so Zscaler/corporate firewalls are bypassed.
#
# Prerequisites:
#   Install session-manager-plugin (one-time):
#     curl -fsSL "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" \
#       -o /tmp/ssm-plugin.deb && sudo dpkg -i /tmp/ssm-plugin.deb
#
# After running:
#   Locust:  make load-test-ui API_HOST=http://localhost:8000
#   MLflow:  open http://localhost:5000
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.instance-info" ] && source "$SCRIPT_DIR/.instance-info"

REGION="${REGION:-us-east-1}"
INSTANCE_ID="${1:-${INSTANCE_ID:-}}"
LOCAL_PORT="${2:-8000}"
REMOTE_PORT="${3:-8000}"

if [ -z "$INSTANCE_ID" ]; then
    echo "ERROR: No instance ID. Run deploy-aws first."
    exit 1
fi

# Check SSM plugin
if ! command -v session-manager-plugin &>/dev/null; then
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  session-manager-plugin not found — install it first:       ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  curl -fsSL \\"
    echo "║    'https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb' \\"
    echo "║    -o /tmp/ssm-plugin.deb"
    echo "║  sudo dpkg -i /tmp/ssm-plugin.deb"
    echo "╚══════════════════════════════════════════════════════════════╝"
    exit 1
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  SSM Port Forwarding                                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  $INSTANCE_ID ($REGION)"
echo "║  localhost:$LOCAL_PORT  →  EC2:$REMOTE_PORT"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  API:    http://localhost:8000/health"
echo "║  Locust: make load-test-ui API_HOST=http://localhost:8000"
echo "║  Press Ctrl-C to stop tunnel"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

aws ssm start-session \
    --region "$REGION" \
    --target "$INSTANCE_ID" \
    --document-name AWS-StartPortForwardingSession \
    --parameters "{\"portNumber\":[\"$REMOTE_PORT\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
