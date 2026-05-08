#!/usr/bin/env bash
# ==============================================================================
# stop-server.sh — Stop/terminate the EC2 instance to avoid charges
#
# Usage:
#   ./deploy/stop-server.sh          # Stop (can restart later)
#   ./deploy/stop-server.sh --terminate  # Permanently delete
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$SCRIPT_DIR/.instance-info" ]; then
    echo "ERROR: No .instance-info found. Run deploy-aws.sh first."
    exit 1
fi

source "$SCRIPT_DIR/.instance-info"

ACTION="${1:---stop}"

case "$ACTION" in
    --stop|-s)
        echo "→ Stopping instance $INSTANCE_ID (can restart with --start)..."
        aws ec2 stop-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
        echo "  ✓ Instance stopping. No further charges for compute."
        echo "  Note: EBS storage still costs ~$0.08/GB/month (30GB = $2.40/month)"
        ;;
    --start)
        echo "→ Starting instance $INSTANCE_ID..."
        aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
        echo "  Waiting for public IP..."
        aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
        NEW_IP=$(aws ec2 describe-instances --region "$REGION" \
            --instance-ids "$INSTANCE_ID" \
            --query "Reservations[0].Instances[0].PublicIpAddress" --output text)
        echo "  ✓ Instance running. New public IP: $NEW_IP"
        echo "  API: http://$NEW_IP:8000/score"
        # Update .instance-info
        sed -i "s/PUBLIC_IP=.*/PUBLIC_IP=$NEW_IP/" "$SCRIPT_DIR/.instance-info"
        ;;
    --terminate|-t)
        echo "⚠  This will PERMANENTLY delete instance $INSTANCE_ID and all its data."
        read -p "   Type 'yes' to confirm: " confirm
        if [ "$confirm" = "yes" ]; then
            aws ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID"
            echo "  ✓ Instance terminated."
            rm -f "$SCRIPT_DIR/.instance-info"
        else
            echo "  Cancelled."
        fi
        ;;
    *)
        echo "Usage: $0 [--stop | --start | --terminate]"
        exit 1
        ;;
esac
