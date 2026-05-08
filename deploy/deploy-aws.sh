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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Workaround for corporate/VPN SSL interception
export AWS_DEFAULT_OUTPUT=json
export PYTHONHTTPSVERIFY=0
export AWS_NO_VERIFY_SSL=true

# ── Configuration ─────────────────────────────────────────────────────────────
REGION="${1:-ap-southeast-1}"          # Singapore (close to ID/MY/PH/TH)
INSTANCE_TYPE="${2:-c5.4xlarge}"       # 16 vCPU, 32 GB — override: make deploy-aws INSTANCE_TYPE=t3.xlarge
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
echo "║  Instance:  $INSTANCE_TYPE"
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
KEY_FILE="$SCRIPT_DIR/${KEY_NAME}.pem"
if ! aws ec2 describe-key-pairs --region "$REGION" --key-names "$KEY_NAME" &>/dev/null; then
    echo "→ Creating SSH key pair: $KEY_NAME"
    aws ec2 create-key-pair --region "$REGION" \
        --key-name "$KEY_NAME" \
        --query "KeyMaterial" --output text > "$KEY_FILE"
    chmod 400 "$KEY_FILE"
    echo "  Saved: $KEY_FILE (keep this safe!)"
else
    echo "→ Key pair '$KEY_NAME' already exists in $REGION"
    if [ ! -f "$KEY_FILE" ]; then
        echo "  WARNING: $KEY_FILE not found locally — you may need to re-create the key pair"
        echo "  Run: aws ec2 delete-key-pair --region $REGION --key-name $KEY_NAME"
        echo "  Then re-run make deploy-aws to generate a new .pem file"
    fi
fi

# ── IAM instance profile (SSM + S3 access, idempotent) ───────────────────────
ROLE_NAME="fraud-demo-ec2-role"
PROFILE_NAME="fraud-demo-ec2-profile"
ACCOUNT_ID=$(aws --no-cli-pager sts get-caller-identity --query Account --output text)

echo "→ Ensuring IAM instance profile ($PROFILE_NAME)..."
if ! aws --no-cli-pager iam get-role --role-name "$ROLE_NAME" &>/dev/null; then
    TRUST_FILE=$(mktemp /tmp/trust-XXXX.json)
    cat > "$TRUST_FILE" << 'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
    aws --no-cli-pager iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document "file://$TRUST_FILE" \
        --description "EC2 role for Fraud ML Demo (SSM + S3)" > /dev/null
    rm -f "$TRUST_FILE"
fi
aws --no-cli-pager iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" 2>/dev/null || true
S3_POL=$(mktemp /tmp/s3pol-XXXX.json)
printf '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],"Resource":["arn:aws:s3:::fraud-demo-deploy-%s","arn:aws:s3:::fraud-demo-deploy-%s/*"]}]}' \
    "$ACCOUNT_ID" "$ACCOUNT_ID" > "$S3_POL"
aws --no-cli-pager iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name "fraud-demo-s3" --policy-document "file://$S3_POL" > /dev/null
rm -f "$S3_POL"
if ! aws --no-cli-pager iam get-instance-profile --instance-profile-name "$PROFILE_NAME" &>/dev/null; then
    aws --no-cli-pager iam create-instance-profile --instance-profile-name "$PROFILE_NAME" > /dev/null
    aws --no-cli-pager iam add-role-to-instance-profile \
        --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME" > /dev/null
    echo "  Created — waiting 10s for IAM propagation..."
    sleep 10
fi
echo "  Ready: $PROFILE_NAME"

# ── User data script (runs on first boot) ────────────────────────────────────
USER_DATA=$(cat <<'CLOUD_INIT'
#!/bin/bash
set -ex

# System updates
apt-get update && apt-get install -y ca-certificates curl gnupg cpulimit unzip make

# Install Docker from official repo (includes compose plugin)
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --batch --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

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
    --iam-instance-profile "Name=$PROFILE_NAME" \
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
