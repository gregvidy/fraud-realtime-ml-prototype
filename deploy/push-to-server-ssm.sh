#!/usr/bin/env bash
# ==============================================================================
# push-to-server-ssm.sh — Deploy project to EC2 via S3 + SSM (no SSH needed)
#
# Prerequisites:
#   make ssm-setup   (attach IAM role to instance — one-time)
#
# What it does:
#   1. Packages the project as a tar.gz (excluding build artifacts)
#   2. Uploads to S3 bucket (fraud-demo-deploy-<ACCOUNT_ID>)
#   3. Sends SSM RunShellScript command to EC2:
#      - Download from S3 (uses IAM instance role)
#      - Extract + chown
#      - docker compose up -d --build
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

[ -f "$SCRIPT_DIR/.instance-info" ] && source "$SCRIPT_DIR/.instance-info"

INSTANCE_ID="${1:-${INSTANCE_ID:-}}"
REGION="${REGION:-us-east-1}"

if [ -z "$INSTANCE_ID" ]; then
    echo "ERROR: No instance ID. Run: make deploy-aws first."
    exit 1
fi

ACCOUNT_ID=$(aws --no-cli-pager sts get-caller-identity --query Account --output text)
S3_BUCKET="fraud-demo-deploy-${ACCOUNT_ID}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
TAR_KEY="deployments/fraud-ml-${TIMESTAMP}.tar.gz"
TAR_FILE="/tmp/fraud-ml-${TIMESTAMP}.tar.gz"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
PARENT_DIR="$(dirname "$PROJECT_DIR")"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Deploying via SSM (no SSH required)                        ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║  Instance: $INSTANCE_ID"
echo "║  Region:   $REGION"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Create S3 bucket ──────────────────────────────────────────────────────────
echo "→ S3 bucket: $S3_BUCKET ..."
# Detect actual bucket region (may differ from instance region)
BUCKET_REGION=$(aws --no-cli-pager s3api get-bucket-location --bucket "$S3_BUCKET" \
    --query LocationConstraint --output text 2>/dev/null || echo "")
if [ -z "$BUCKET_REGION" ] || [ "$BUCKET_REGION" = "None" ] || [ "$BUCKET_REGION" = "null" ]; then
    BUCKET_REGION="us-east-1"
fi

if ! aws --no-cli-pager s3api head-bucket --region "$BUCKET_REGION" --bucket "$S3_BUCKET" 2>/dev/null; then
    if [ "$REGION" = "us-east-1" ]; then
        aws --no-cli-pager s3api create-bucket --region "$REGION" --bucket "$S3_BUCKET" > /dev/null
    else
        aws --no-cli-pager s3api create-bucket --region "$REGION" --bucket "$S3_BUCKET" \
            --create-bucket-configuration LocationConstraint="$REGION" > /dev/null
    fi
    BUCKET_REGION="$REGION"
    echo "  Created: s3://$S3_BUCKET (region: $BUCKET_REGION)"
else
    echo "  Exists:  s3://$S3_BUCKET (region: $BUCKET_REGION)"
fi

# ── Package project ───────────────────────────────────────────────────────────
echo "→ Packaging project..."
tar czf "$TAR_FILE" \
    --exclude=".git" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude="mlruns" \
    --exclude="mlflow.db" \
    --exclude="data/duckdb/*.duckdb" \
    --exclude="data/duckdb/*.wal" \
    --exclude="data/duckdb/parquet" \
    --exclude="training/datasets" \
    --exclude="models/*.pkl" \
    --exclude="models/*.joblib" \
    --exclude="*.log" \
    -C "$PARENT_DIR" "$PROJECT_NAME"

echo "  Size: $(du -sh "$TAR_FILE" | cut -f1)"

# ── Upload to S3 ──────────────────────────────────────────────────────────────
echo "→ Uploading to s3://$S3_BUCKET/$TAR_KEY ..."
aws s3 cp "$TAR_FILE" "s3://$S3_BUCKET/$TAR_KEY" --region "$BUCKET_REGION"
rm -f "$TAR_FILE"
echo "  Done."

# ── Generate pre-signed URL (must use bucket's region, not instance region) ────
echo "→ Generating pre-signed download URL (1 hour)..."
PRESIGNED_URL=$(aws s3 presign "s3://$S3_BUCKET/$TAR_KEY" \
    --region "$BUCKET_REGION" --expires-in 3600)

# ── Send SSM deploy command ───────────────────────────────────────────────────
echo "→ Sending deploy command via SSM..."
PARAMS_FILE=$(mktemp /tmp/ssm-params-XXXX.json)

# Build JSON params using python3 (handles any special chars in paths/vars)
python3 - "$PRESIGNED_URL" > "$PARAMS_FILE" << 'PYEOF'
import sys, json

presigned_url = sys.argv[1]

commands = [
    "set -ex",
    # Ensure Docker + Compose plugin are installed (idempotent) — use Docker official repo
    "if ! docker compose version &>/dev/null; then apt-get remove -y docker.io docker-doc docker-compose containerd runc 2>/dev/null || true && apt-get update && apt-get install -y ca-certificates curl gnupg make unzip && install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --batch --dearmor -o /etc/apt/keyrings/docker.gpg && chmod a+r /etc/apt/keyrings/docker.gpg && echo \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list && apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin && systemctl enable docker && systemctl start docker && usermod -aG docker ubuntu; fi",
    # Ensure make + AWS CLI available
    "command -v make &>/dev/null || (apt-get update && apt-get install -y make)",
    "if ! command -v aws &>/dev/null; then curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip && cd /tmp && unzip -qo awscliv2.zip && ./aws/install && rm -rf /tmp/aws /tmp/awscliv2.zip; fi",
    # Wait for Docker daemon to be ready
    "systemctl is-active --quiet docker || systemctl start docker",
    f"curl -fsSL -o /tmp/deploy.tar.gz '{presigned_url}'",
    "rm -rf /home/ubuntu/fraud-realtime-ml-prototype",
    "mkdir -p /home/ubuntu",
    "tar xzf /tmp/deploy.tar.gz -C /home/ubuntu/",
    "chown -R ubuntu:ubuntu /home/ubuntu/fraud-realtime-ml-prototype",
    "chmod o+x /home/ubuntu",
    # Ensure ssm-user can run docker commands
    "usermod -aG docker ssm-user 2>/dev/null || true",
    # Pull ML artifacts from S3 (parquet, model, registry)
    "cd /home/ubuntu/fraud-realtime-ml-prototype && chmod +x deploy/pull-artifacts.sh && REGION=us-east-1 deploy/pull-artifacts.sh",
    # Build all images (API + training + simulator)
    "cd /home/ubuntu/fraud-realtime-ml-prototype && docker compose -f deploy/docker-compose.prod.yml --profile simulator --profile training build",
    # Start core services (API + Postgres + Redis)
    "cd /home/ubuntu/fraud-realtime-ml-prototype && docker compose -f deploy/docker-compose.prod.yml up -d --build",
    # Materialize features from parquet into Redis (feast apply + materialize)
    "cd /home/ubuntu/fraud-realtime-ml-prototype && docker compose -f deploy/docker-compose.prod.yml --profile training run --rm -T training sh -c 'cd feast_repo/feature_repo && feast apply && cd /app && python scripts/materialize_features.py --days 0 --skip-export'",
    "echo '=== Deploy complete ==='",
    "echo 'Simulator: make stream-docker EPS=20  |  Training: make train-docker'",
    "docker ps",
]

print(json.dumps({
    "commands": commands,
    "executionTimeout": ["3600"]
}))
PYEOF

COMMAND_ID=$(aws --no-cli-pager ssm send-command \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters "file://$PARAMS_FILE" \
    --comment "fraud-ml deploy $TIMESTAMP" \
    --query "Command.CommandId" --output text)
rm -f "$PARAMS_FILE"

echo "  Command ID: $COMMAND_ID"
echo "→ Waiting for completion (first Docker build takes ~10 min)..."

for i in $(seq 1 80); do
    STATUS=$(aws --no-cli-pager ssm get-command-invocation \
        --region "$REGION" \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" \
        --query "Status" --output text 2>/dev/null || echo "Pending")

    case "$STATUS" in
        Success)
            echo ""
            echo "  ✓ Deploy succeeded!"
            echo ""
            aws --no-cli-pager ssm get-command-invocation \
                --region "$REGION" \
                --command-id "$COMMAND_ID" \
                --instance-id "$INSTANCE_ID" \
                --query "StandardOutputContent" --output text | tail -20
            echo ""
            echo "╔══════════════════════════════════════════════════════════════╗"
            echo "║  Next steps:                                                ║"
            echo "╠══════════════════════════════════════════════════════════════╣"
            echo "║  Port-forward API:  make ssm-tunnel                         ║"
            echo "║  Load test:         make load-test-ui API_HOST=http://localhost:8000 ║"
            echo "║  Interactive shell: make ssm-shell                          ║"
            echo "╚══════════════════════════════════════════════════════════════╝"
            exit 0
            ;;
        Failed|TimedOut|Cancelled)
            echo ""
            echo "  ✗ Deploy $STATUS"
            echo "--- stdout ---"
            aws --no-cli-pager ssm get-command-invocation \
                --region "$REGION" \
                --command-id "$COMMAND_ID" \
                --instance-id "$INSTANCE_ID" \
                --query "StandardOutputContent" --output text | tail -30
            echo "--- stderr ---"
            aws --no-cli-pager ssm get-command-invocation \
                --region "$REGION" \
                --command-id "$COMMAND_ID" \
                --instance-id "$INSTANCE_ID" \
                --query "StandardErrorContent" --output text | tail -30
            exit 1
            ;;
    esac

    printf "  [%2d/80] %-12s — waiting 15s...\r" "$i" "$STATUS"
    sleep 15
done

echo ""
echo "  Timed out. Check status manually:"
echo "  aws --no-cli-pager ssm get-command-invocation --region $REGION \\"
echo "    --command-id $COMMAND_ID --instance-id $INSTANCE_ID"
exit 1
