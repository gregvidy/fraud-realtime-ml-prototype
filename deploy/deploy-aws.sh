#!/usr/bin/env bash
# ==============================================================================
# deploy-aws.sh — One-command deployment to AWS EC2
#
# Prerequisites:
#   1. AWS CLI installed + configured (aws configure)
#   2. SSH key pair created in AWS Console (default: fraud-demo-key)
#
# Usage:
#   chmod +x deploy/deploy-aws.sh
#   ./deploy/deploy-aws.sh          # Deploy with defaults (ap-southeast-1)
#   ./deploy/deploy-aws.sh us-east-1  # Deploy to specific region
#
# After deployment:
#   - API endpoint: http://<PUBLIC_IP>:8000/score
#   - Run load test: make load-test API_HOST=http://<PUBLIC_IP>:8000
# ==============================================================================
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
REGION="${1:-ap-southeast-1}"          # Singapore (close to ID/MY/PH/TH)
INSTANCE_TYPE="c5.2xlarge"             # 8 vCPU, 16 GB — compute optimized
KEY_NAME="${AWS_KEY_NAME:-fraud-demo-key}"
SECURITY_GROUP_NAME="fraud-demo-sg"
INSTANCE_NAME="fraud-ml-demo"

# Ubuntu 22.04 LTS AMIs (by region)
declare -A AMIS=(
    ["ap-southeast-1"]="ami-0672fd5b9210aa093"   # Singapore
    ["ap-southeast-3"]="ami-0a9c8a0b5e5e5b5a5"   # Jakarta
    ["us-east-1"]="ami-0c7217cdde317cfec"         # Virginia
    ["us-west-2"]="ami-008fe2fc65df48dac"         # Oregon
    ["eu-west-1"]="ami-0905a3c97561e0b69"         # Ireland
    ["ap-northeast-1"]="ami-0d52744d6551d851e"    # Tokyo
)

AMI_ID="${AMIS[$REGION]:-ami-0c7217cdde317cfec}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Fraud ML Demo — AWS Deployment                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Region:    $REGION"
echo "║  Instance:  $INSTANCE_TYPE (8 vCPU, 16GB)"
echo "║  Cost:      ~\$0.34/hr (stop when not demoing!)"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Check prerequisites ───────────────────────────────────────────────────────
if ! command -v aws &>/dev/null; then
    echo "ERROR: AWS CLI not installed. Run: pip install awscli && aws configure"
    exit 1
fi

if ! aws sts get-caller-identity &>/dev/null; then
    echo "ERROR: AWS not configured. Run: aws configure"
    exit 1
fi

# ── Create security group ─────────────────────────────────────────────────────
echo "→ Creating security group..."
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" \
    --filters "Name=isDefault,Values=true" \
    --query "Vpcs[0].VpcId" --output text)

SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=$SECURITY_GROUP_NAME" "Name=vpc-id,Values=$VPC_ID" \
    --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
    SG_ID=$(aws ec2 create-security-group --region "$REGION" \
        --group-name "$SECURITY_GROUP_NAME" \
        --description "Fraud ML Demo - API + SSH" \
        --vpc-id "$VPC_ID" \
        --query "GroupId" --output text)

    # SSH (restricted to your IP)
    MY_IP=$(curl -s https://checkip.amazonaws.com)
    aws ec2 authorize-security-group-ingress --region "$REGION" \
        --group-id "$SG_ID" --protocol tcp --port 22 --cidr "${MY_IP}/32"

    # API port
    aws ec2 authorize-security-group-ingress --region "$REGION" \
        --group-id "$SG_ID" --protocol tcp --port 8000 --cidr "0.0.0.0/0"

    # Locust UI (optional)
    aws ec2 authorize-security-group-ingress --region "$REGION" \
        --group-id "$SG_ID" --protocol tcp --port 8089 --cidr "0.0.0.0/0"

    echo "  Created: $SG_ID (SSH from $MY_IP, API open)"
else
    echo "  Reusing: $SG_ID"
fi

# ── Check/create key pair ─────────────────────────────────────────────────────
if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" &>/dev/null; then
    echo "→ Creating SSH key pair: $KEY_NAME"
    aws ec2 create-key-pair --region "$REGION" \
        --key-name "$KEY_NAME" \
        --query "KeyMaterial" --output text > "${KEY_NAME}.pem"
    chmod 400 "${KEY_NAME}.pem"
    echo "  Saved: ${KEY_NAME}.pem (keep this safe!)"
else
    echo "→ Key pair '$KEY_NAME' already exists"
fi

# ── User data script (runs on first boot) ────────────────────────────────────
USER_DATA=$(cat <<'CLOUD_INIT'
#!/bin/bash
set -ex

# System updates
apt-get update && apt-get install -y docker.io docker-compose-plugin git

# Start Docker
systemctl enable docker && systemctl start docker
usermod -aG docker ubuntu

# Clone/setup project directory
mkdir -p /opt/fraud-demo && cd /opt/fraud-demo

echo "=== Cloud-init complete — ready for deployment ==="
CLOUD_INIT
)

# ── Launch instance ───────────────────────────────────────────────────────────
echo "→ Launching $INSTANCE_TYPE instance..."
INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --user-data "$USER_DATA" \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
    --query "Instances[0].InstanceId" --output text)

echo "  Instance: $INSTANCE_ID"
echo "→ Waiting for instance to be running..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓ Instance launched successfully!                          ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Public IP:  $PUBLIC_IP"
echo "║  Instance:   $INSTANCE_ID"
echo "║                                                              "
echo "║  Wait ~2 min for cloud-init, then:                          ║"
echo "║                                                              "
echo "║  1. Deploy:                                                  ║"
echo "║     ./deploy/push-to-server.sh $PUBLIC_IP                   "
echo "║                                                              "
echo "║  2. SSH:                                                     ║"
echo "║     ssh -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP                "
echo "║                                                              "
echo "║  3. Load test (from your laptop):                            ║"
echo "║     make load-test API_HOST=http://$PUBLIC_IP:8000          "
echo "║                                                              "
echo "║  ⚠ STOP when done (\$0.34/hr):                              ║"
echo "║     aws ec2 stop-instances --instance-ids $INSTANCE_ID      "
echo "╚══════════════════════════════════════════════════════════════╝"

# Save instance info for later use
cat > deploy/.instance-info <<EOF
INSTANCE_ID=$INSTANCE_ID
PUBLIC_IP=$PUBLIC_IP
REGION=$REGION
KEY_NAME=$KEY_NAME
EOF

echo ""
echo "Instance info saved to deploy/.instance-info"
