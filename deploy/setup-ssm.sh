#!/usr/bin/env bash
# ==============================================================================
# setup-ssm.sh — Create IAM role + attach to running instance (one-time setup)
#
# After this, you can deploy without SSH:
#   make deploy-push          # upload via S3 + SSM
#   make ssm-shell            # interactive terminal over HTTPS
#   make ssm-tunnel           # port-forward API to localhost:8000
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/.instance-info" ] && source "$SCRIPT_DIR/.instance-info"

REGION="${REGION:-us-east-1}"
INSTANCE_ID="${1:-${INSTANCE_ID:-}}"
ROLE_NAME="fraud-demo-ec2-role"
PROFILE_NAME="fraud-demo-ec2-profile"

if [ -z "$INSTANCE_ID" ]; then
    echo "ERROR: No instance ID. Run deploy-aws first, or pass instance ID as argument."
    exit 1
fi

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Setting up SSM access for $INSTANCE_ID"
echo "╚══════════════════════════════════════════════════════════════╝"

ACCOUNT_ID=$(aws --no-cli-pager sts get-caller-identity --query Account --output text)

# ── IAM role ──────────────────────────────────────────────────────────────────
echo "→ IAM role: $ROLE_NAME ..."
if ! aws --no-cli-pager iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
    TRUST_FILE=$(mktemp /tmp/trust-XXXX.json)
    cat > "$TRUST_FILE" << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "ec2.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
EOF
    aws --no-cli-pager iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "file://$TRUST_FILE" \
        --description "EC2 role for Fraud ML Demo (SSM + S3)" > /dev/null
    rm -f "$TRUST_FILE"
    echo "  Created: $ROLE_NAME"
else
    echo "  Exists:  $ROLE_NAME"
fi

# Attach SSM managed policy
aws --no-cli-pager iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" 2>/dev/null || true
echo "  Attached: AmazonSSMManagedInstanceCore"

# Inline policy for deploy S3 bucket
S3_POLICY_FILE=$(mktemp /tmp/s3-policy-XXXX.json)
cat > "$S3_POLICY_FILE" << EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject", "s3:ListBucket"],
    "Resource": [
      "arn:aws:s3:::fraud-demo-deploy-${ACCOUNT_ID}",
      "arn:aws:s3:::fraud-demo-deploy-${ACCOUNT_ID}/*"
    ]
  }]
}
EOF
aws --no-cli-pager iam put-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-name "fraud-demo-s3-deploy-access" \
    --policy-document "file://$S3_POLICY_FILE" > /dev/null
rm -f "$S3_POLICY_FILE"
echo "  Attached: S3 access (fraud-demo-deploy-${ACCOUNT_ID})"

# ── Instance profile ──────────────────────────────────────────────────────────
echo "→ Instance profile: $PROFILE_NAME ..."
if ! aws --no-cli-pager iam get-instance-profile --instance-profile-name "$PROFILE_NAME" &>/dev/null; then
    aws --no-cli-pager iam create-instance-profile \
        --instance-profile-name "$PROFILE_NAME" > /dev/null
    aws --no-cli-pager iam add-role-to-instance-profile \
        --instance-profile-name "$PROFILE_NAME" \
        --role-name "$ROLE_NAME"
    echo "  Created and configured: $PROFILE_NAME"
    echo "  Waiting 10s for IAM propagation..."
    sleep 10
else
    echo "  Exists: $PROFILE_NAME"
fi

# ── Attach profile to instance ────────────────────────────────────────────────
echo "→ Attaching profile to instance $INSTANCE_ID ..."
EXISTING=$(aws --no-cli-pager ec2 describe-iam-instance-profile-associations \
    --region "$REGION" \
    --filters "Name=instance-id,Values=$INSTANCE_ID" \
    --query "IamInstanceProfileAssociations[0].AssociationId" \
    --output text 2>/dev/null || echo "None")

if [ "$EXISTING" = "None" ] || [ -z "$EXISTING" ]; then
    aws --no-cli-pager ec2 associate-iam-instance-profile \
        --region "$REGION" \
        --instance-id "$INSTANCE_ID" \
        --iam-instance-profile Name="$PROFILE_NAME" > /dev/null
    echo "  Attached."
else
    echo "  Already attached (assoc: $EXISTING)"
fi

# ── Wait for SSM agent to come online ────────────────────────────────────────
echo "→ Waiting for SSM agent to register (up to 3 min)..."
for i in $(seq 1 36); do
    PING=$(aws --no-cli-pager ssm describe-instance-information \
        --region "$REGION" \
        --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
        --query "InstanceInformationList[0].PingStatus" \
        --output text 2>/dev/null || echo "None")

    if [ "$PING" = "Online" ]; then
        echo ""
        echo "╔══════════════════════════════════════════════════════════════╗"
        echo "║  ✓ SSM agent online — ready to deploy!                      ║"
        echo "╠══════════════════════════════════════════════════════════════╣"
        echo "║  Deploy code:   make deploy-push                            ║"
        echo "║  Shell access:  make ssm-shell                              ║"
        echo "║  Port forward:  make ssm-tunnel                             ║"
        echo "╚══════════════════════════════════════════════════════════════╝"
        exit 0
    fi
    printf "  [%2d/36] SSM status: %-8s — waiting 5s...\r" "$i" "$PING"
    sleep 5
done

echo ""
echo "  SSM not yet online. Try again in a minute:"
echo "  aws --no-cli-pager ssm describe-instance-information --region $REGION --filters 'Key=InstanceIds,Values=$INSTANCE_ID'"
