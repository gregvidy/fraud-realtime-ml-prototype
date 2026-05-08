# AWS Deployment Plan — Fraud Real-Time ML Demo

---

## 1. AWS Account Setup

### 1a. Create Account
1. Go to https://aws.amazon.com/free/ → **"Create a Free Account"**
2. Enter email + password + account name → choose **Personal** account type
3. Enter credit card (required; won't be charged under Free Tier)
4. Complete phone verification → select **Basic Support** (free)

### 1b. Create IAM User for CLI (do NOT use root keys)
1. Console → **IAM** → **Users** → **Create user**
2. Username: `fraudml-demo-admin` — leave "Console access" **unchecked**
3. Permissions: **Attach policies directly** → select `AdministratorAccess`
4. After user is created → **Security credentials** tab → **Create access key**
5. Use case: **Command Line Interface (CLI)** → check the confirmation box
6. Description tag: `fraud-demo-cli` (optional) → **Create access key**
7. **Download the CSV** — you will not be able to see the secret key again

### 1c. Install AWS CLI

**Linux:**
```bash
# Download + extract (unzip may need installing first)
sudo apt-get install -y unzip
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
unzip /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install
```

**Linux (if path has spaces — copy to /tmp first):**
```bash
cp -r ./aws /tmp/aws-cli
sudo /tmp/aws-cli/install
```

**Mac (Homebrew — recommended):**
```bash
brew install awscli
```

**Mac (official installer):**
```bash
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
sudo installer -pkg AWSCLIV2.pkg -target /
```

**Verify:**
```bash
aws --version
# aws-cli/2.x.x ...
```

### 1d. Configure CLI
```bash
aws configure
# AWS Access Key ID:     <from CSV downloaded in step 1b>
# AWS Secret Access Key: <from CSV downloaded in step 1b>
# Default region:        us-east-1
# Default output format: json

# Verify — should show your account ID and fraudml-demo-admin
aws sts get-caller-identity
```

### 1e. Set Billing Alarm (important — avoids surprise charges)
1. Console → **Billing** → **Budgets** → **Create budget**
2. Choose **Monthly cost budget** → set amount: `$10`
3. Add email alert at 80% threshold

---

## 2. Cost Reference

| Instance | vCPU | RAM | Cost/hr | Use case |
|----------|------|-----|---------|----------|
| `t3.micro` | 2 | 1 GB | **Free tier** | Architecture demo only, low RPS |
| `t3.xlarge` | 4 | 16 GB | $0.166/hr | API only, no concurrent training |
| `c5.2xlarge` | 8 | 16 GB | $0.340/hr | API + light training |
| **`c5.4xlarge`** | **16** | **32 GB** | **$0.680/hr** | **Full demo (recommended)** |

**Full concurrent demo on `c5.4xlarge` for 2 hours: ~$1.40**

> **IMPORTANT**: Always stop the instance after the demo — `c5.4xlarge` costs ~$490/month if left running.

### Why c5.4xlarge for the full demo?
When all services run concurrently, total Docker resource budget is:

| Container | CPU limit | RAM limit |
|-----------|-----------|-----------|
| `fraud_api` | 12 cores | 8 GB |
| `fraud_postgres` | 1 core | 1 GB |
| `fraud_redis` | 1 core | 768 MB |
| `fraud_locust` | 2 cores | 512 MB |
| `fraud_simulator` | 0.5 core | 256 MB |
| `fraud_training` | 4 cores | 6 GB |
| **Total** | **20.5 cores** | **~16.5 GB** |

`c5.4xlarge` has 16 cores / 32 GB — plenty of headroom.

---

## 3. Deployment Architecture

Deployment uses **AWS Systems Manager (SSM)** — no SSH required. This is more secure
(no open port 22, no key management) and works through corporate firewalls/VPNs.

Data pipeline runs **locally**, artifacts flow through **S3**, and EC2 just **serves**.

### How it works

```
Local (your machine)                    S3                              EC2
┌────────────────────────┐    push    ┌──────────────────┐    pull    ┌──────────────────┐
│ make seed-data         │           │ /artifacts/      │           │ API (FastAPI)    │
│ make offline-pipeline  │──────────→│   parquet/       │──────────→│ Redis (features) │
│ make train             │           │   models/        │           │ Postgres (scores)│
│ make push-artifacts    │           │   duckdb/        │           │                  │
│                        │           │   feast/         │           │ feast apply      │
│ make deploy-push ──────│── code ──→│ /deployments/    │──── SSM ─→│ materialize      │
└────────────────────────┘           └──────────────────┘           └──────────────────┘
```

**What stays local:** `seed-data`, `offline-pipeline`, `dbt-run`, `train`, `push-artifacts`
**What runs on EC2:** API serving, Redis, Postgres (score logs + reference data), simulator, training (optional)

### S3 Artifact Flow

| Artifact | Local path | S3 key | Purpose |
|----------|-----------|--------|---------|
| Parquet features | `data/duckdb/parquet/*.parquet` | `artifacts/parquet/` | Feast materialization source |
| Trained model | `models/*.pkl` + `model_meta.json` | `artifacts/models/` | Scoring endpoint |
| Feast registry | `feast_repo/feature_repo/data/registry.db` | `artifacts/feast/` | Feature store metadata |
| DuckDB database | `data/duckdb/fraud_offline.duckdb` | `artifacts/duckdb/` | Remote training (optional) |

### Deploy scripts

| Script | Called by | What it does |
|--------|-----------|-------------|
| `push-artifacts.sh` | `make push-artifacts` | Upload local artifacts → S3 |
| `pull-artifacts.sh` | `make deploy-push` (on EC2) | Download S3 artifacts → EC2 project dir |
| `push-to-server-ssm.sh` | `make deploy-push` | Package code → S3 → SSM → pull artifacts → docker compose up |
| `init-remote-db.sh` | `make deploy-init` | One-time: seed EC2 Postgres with reference + historical data |

### IAM roles & policies

| Resource | Purpose |
|----------|---------|
| IAM Role: `fraud-demo-ec2-role` | EC2 instance role |
| Policy: `AmazonSSMManagedInstanceCore` | Allows SSM agent to communicate with SSM service |
| Inline policy: `fraud-demo-s3-deploy-access` | Allows instance to read from the deploy S3 bucket |
| Instance Profile: `fraud-demo-ec2-profile` | Attaches the role to the EC2 instance |

---

## 4. Deploy — Step by Step

### First-time setup

```bash
cd fraud-realtime-ml-prototype

# ── Local: build data + model ─────────────────────────────────────────────────

# Step 1: Start local Postgres + Redis
make infra-up

# Step 2: Generate synthetic data
make seed-data
# Or customize:  make seed-data START_DATE=2025-01-01 END_DATE=2026-03-31

# Step 3: Full offline pipeline (Postgres → DuckDB → dbt → parquet → Redis)
make offline-pipeline

# Step 4: Train model
make train
# Or with config:  make train CONFIG=training/experiments/xgboost_v1.yaml

# Step 5: Push artifacts to S3 (parquet + model + registry + DuckDB)
make push-artifacts

# ── AWS: provision + deploy ───────────────────────────────────────────────────

# Step 6: Provision EC2 instance + IAM roles + security group (~2 min)
make deploy-aws

# Step 7: (Optional) Set up SSM access if not auto-attached
make ssm-setup

# Step 8: Deploy code + pull artifacts from S3 + start services (~5-10 min)
make deploy-push

# Step 9: Seed EC2 Postgres with reference data (one-time, for simulator)
make deploy-init

# Step 10: Port-forward API to localhost
make ssm-tunnel
# → API available at http://localhost:8000

# Step 11: Verify
curl http://localhost:8000/health
```

### Subsequent deploys (after local changes)

```bash
# If you changed data, model, or features:
make push-artifacts

# Deploy updated code + pull latest artifacts:
make deploy-push

# That's it — no need to re-run deploy-init
```

### Override defaults
```bash
# Deploy to a different region
make deploy-aws REGION=us-east-1        # Virginia (cheapest, default)

# Use a smaller/larger instance
make deploy-aws INSTANCE_TYPE=t3.xlarge     # Smaller, cheaper
make deploy-aws INSTANCE_TYPE=c5.4xlarge    # Full demo (recommended)
```

### Docker installation on EC2

Docker is installed from **Docker's official apt repository** (not Ubuntu's `docker.io` package).
This is handled automatically in two places:

1. **Cloud-init** (`deploy-aws.sh`): Pre-installs Docker on instance creation
2. **SSM deploy** (`push-to-server-ssm.sh`): Checks `docker compose version` on every deploy —
   if Docker or the Compose plugin is missing, installs from scratch

Packages installed: `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-compose-plugin`

---

## 5. SSM Access Commands

All remote access goes through SSM (HTTPS) — no SSH keys or open ports needed.

| Command | What it does |
|---------|-------------|
| `make ssm-shell` | Interactive shell on EC2 (like SSH, but over HTTPS) |
| `make ssm-tunnel` | Port-forward EC2 port 8000 → localhost:8000 (API) |
| `make ssm-tunnel-mlflow` | Port-forward EC2 port 5000 → localhost:5000 (MLflow) |
| `make ssm-tunnel-locust` | Port-forward EC2 port 8089 → localhost:8089 (Locust UI) |
| `make start-remote-locust` | Start Locust container on EC2 (runs load test on-instance) |
| `make ssm-setup` | One-time: attach IAM role to instance for SSM access |

### Why SSM instead of SSH?

Corporate Zscaler / VPN blocks SSH (port 22). SSM uses **HTTPS over port 443** —
passes through all firewalls. Trade-off: tunnel adds ~100-200ms latency per API
request, so load tests through the tunnel show inflated numbers. This is solved
by running Locust directly on EC2 (see Section 6).

---

## 6. Full Concurrent Demo

### Understanding latency measurement

**IMPORTANT:** The SSM tunnel adds ~100-200ms network overhead per request.
Running Locust from your laptop via `make ssm-tunnel` shows **tunnel latency**,
not API latency.

To show **true server-side performance**, Locust runs as a Docker container
directly on EC2, hitting the API over Docker's internal network (~0ms hop).
You only tunnel the Locust **dashboard** (lightweight HTML/WebSocket) to your
browser — this does NOT affect the load test numbers.

```
Your browser ──SSM tunnel (8089)──► Locust Dashboard (EC2:8089)
                                        │
                                        ▼  Docker network: http://api:8000
                                    Fraud API (EC2) ← TRUE latency measured here
```

The Locust container has `LOCUST_SERVER_TIME=1` set in `docker-compose.prod.yml`.
This makes Locust report the `X-Process-Time-Ms` response header as latency
instead of network round-trip time. The charts and percentile tables in the
Locust UI reflect **actual server-side processing time**.

You can also verify manually with curl:
```bash
# Through SSM tunnel — header shows true time even if curl takes 200ms+
curl -s -D - http://localhost:8000/score \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"t1","user_id":"u_000001","device_id":"d_0000001","merchant_id":"m_00001","amount":250,"currency":"USD","payment_method":"card","country_code":"US","is_international":false}' \
  | grep -i "x-process-time"
# → X-Process-Time-Ms: 7.2   (true server time)
```

### Demo setup — step by step

#### Prerequisites

Ensure the API is deployed and running:
```bash
# If not already deployed:
make push-artifacts && make deploy-push

# Verify API is healthy (through SSM tunnel or SSM shell)
make ssm-tunnel
curl http://localhost:8000/health
# → {"status":"ok","model_loaded":true,"redis_connected":true}
```

#### Step 1 — Start Locust on EC2

```bash
make start-remote-locust
```

This runs an SSM command that starts the Locust Docker container on EC2 with:
- `LOCUST_SERVER_TIME=1` (reports server-side latency in UI)
- Volume mount of `locustfile.py` (no rebuild needed for locust changes)
- `--host http://api:8000` pre-configured (Docker internal network)
- `--force-recreate` to pick up any config changes

#### Step 2 — Tunnel Locust UI to your browser

```bash
make ssm-tunnel-locust
```

This port-forwards EC2:8089 → localhost:8089 via SSM (HTTPS, no SSH).

#### Step 3 — Open Locust UI

Open **http://localhost:8089** in your browser.

#### Step 4 — Configure and start the load test

| Setting | Value | Rationale |
|---------|-------|-----------|
| Number of users | **500** | 500 users × 2 req/s each = ~1000 RPS |
| Ramp up (users/s) | **50** | Reaches full load in 10 seconds |
| Host | `http://api:8000` | Pre-filled, do not change |

Click **Start**.

#### Step 5 — Show the results

Once the ramp-up completes (~10s), the Locust UI shows:

| Metric | Expected value | Notes |
|--------|---------------|-------|
| **RPS** | ~925-1000 | Sustained throughput |
| **p50** | **~20ms** | Server-side processing time |
| **p95** | **~38ms** | Well under 100ms target |
| **p99** | **~77ms** | Even tail latency is fast |
| **max** | **~170ms** | No catastrophic outliers |
| **Failures** | 0% | Zero HTTP errors |

> These numbers are validated on a c5.4xlarge (16 vCPU, 32 GB RAM) with
> 12 Gunicorn/Uvicorn workers serving LightGBM + Redis features.

#### Step 6 (optional) — Ad-hoc curl demos

In a separate terminal:
```bash
make ssm-tunnel
# → localhost:8000 forwards to API

curl -s http://localhost:8000/score \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"demo-1","user_id":"u_000042","device_id":"d_0000123","merchant_id":"m_00005","amount":1500,"is_international":true}' | python3 -m json.tool
```

Shows the full JSON response with `score`, `risk_band`, `is_flagged`, and
`feature_sources` — while the load test continues running.

### Full concurrent demo (API + training + streaming)

All services run simultaneously on the same c5.4xlarge instance, demonstrating
resource isolation between the serving plane and the training plane.

#### On your laptop (3 terminals)

```bash
# Terminal 1 — Locust dashboard (load test runs ON EC2)
make ssm-tunnel-locust
# → Open http://localhost:8089

# Terminal 2 — MLflow UI (training experiment tracking)
make ssm-tunnel-mlflow
# → Open http://localhost:5000

# Terminal 3 — API for ad-hoc curl requests
make ssm-tunnel
# → curl http://localhost:8000/health
```

#### On EC2 (via SSM shell)

```bash
make ssm-shell
cd /home/ubuntu/fraud-realtime-ml-prototype
```

```bash
# Start event simulator (streams 20 txn/sec into Redis online features)
docker compose -f deploy/docker-compose.prod.yml --profile simulator up -d simulator

# Start training pipeline (isolated: 4 CPU / 6GB limit, does not affect API)
docker compose -f deploy/docker-compose.prod.yml --profile training run --rm training

# Watch live CPU/RAM per container
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

### What the audience sees simultaneously

| Window | Shows |
|--------|-------|
| **Locust UI** | RPS stable at ~1000, **p50 ~20ms, p95 ~38ms** — even while training runs |
| `docker stats` | Serving plane: ~12 CPU. Training plane: ~4 CPU. Completely separated |
| MLflow | Training runs appearing in real-time with metrics + artifacts |
| `curl` + `X-Process-Time-Ms` | Proves server-side latency independent of network |

### Validated benchmark results (c5.4xlarge, 12 workers)

| Test method | p50 | p95 | p99 | max | RPS | Notes |
|---|---|---|---|---|---|---|
| Laptop → SSM tunnel | ~220ms | ~320ms | ~400ms | ~500ms | ~960 | +200ms SSM overhead per request |
| **Locust on EC2 (headless)** | **20ms** | **38ms** | **77ms** | **170ms** | **~925** | **True API latency** |
| **Locust on EC2 (web UI)** | **~20ms** | **~38ms** | **~77ms** | **~170ms** | **~925** | **Same — uses `LOCUST_SERVER_TIME=1`** |

### Key optimizations enabling these numbers

| Optimization | Impact |
|---|---|
| All CPU work (vector assembly + predict) in ThreadPoolExecutor | Event loop freed for async Redis I/O — eliminated ~200ms scheduling delay |
| Gunicorn access log disabled (`/dev/null`) | Removed per-request stdout I/O blocking event loop |
| orjson response serialization | ~5× faster JSON encoding at 1000 RPS |
| 16-thread predict pool per worker | Eliminated thread pool queueing under load |
| LightGBM isotonic calibration extracted at load time | Bypasses CalibratedClassifierCV overhead (~15-40ms → ~3ms) |
| Pipelined Redis commands (11 ZRANGEBYSCORE in 1 pipeline) | Single round-trip for all online features |
| Direct Redis reads for Feast (bypass SDK) | mmh3 key hashing + binary HMGET, ~2ms for 3 entity types |
| Per-entity TTL caching (60s) | Eliminates redundant Redis calls for repeated users/devices |

---

## 7. After the Demo — Stop the Instance

```bash
# Stop (keeps data, stops compute charges — can restart later)
make deploy-stop

# Restart a stopped instance
make deploy-start

# Re-deploy after restart (re-pushes code + rebuilds containers)
make deploy-push

# Terminate permanently (deletes everything, no further charges)
make deploy-terminate
```

---

## 8. Troubleshooting

### `docker: not found` during deploy-push
The SSM deploy script auto-installs Docker from Docker's official apt repo if not present.
If this fails, SSM shell into the instance and install manually:
```bash
make ssm-shell
# Then on the instance:
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --batch --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable docker && sudo systemctl start docker
sudo usermod -aG docker ubuntu
```
Then re-run `make deploy-push`.

### `Unable to locate package docker-compose-plugin`
Docker was installed from Ubuntu's default repo (`docker.io`) instead of Docker's
official repo. The deploy script handles this by removing `docker.io` and reinstalling
from the official repo.

### Permission denied when pulling artifacts
Docker volume mounts create directories as root. Fix ownership:
```bash
make ssm-shell
cd /home/ubuntu/fraud-realtime-ml-prototype
sudo chown -R ubuntu:ubuntu data/ models/ feast_repo/
```

### DuckDB not found during `make train-docker`
The DuckDB file needs to be pulled from S3 to EC2. It's included in `pull-artifacts.sh`
and runs automatically during `make deploy-push`. If missing, pull manually:
```bash
make ssm-shell
cd /home/ubuntu/fraud-realtime-ml-prototype
sudo aws s3 cp s3://fraud-demo-deploy-<ACCOUNT_ID>/artifacts/duckdb/fraud_offline.duckdb data/duckdb/fraud_offline.duckdb
sudo chown ubuntu:ubuntu data/duckdb/fraud_offline.duckdb
```

### Feast `ValueError: invalid literal for int()` (Redis connection)
`feature_store.yaml` uses `${REDIS_HOST}:${REDIS_PORT}` env var substitution.
Feast does **not** support shell-style defaults (`${VAR:-default}`). Ensure env vars
are always set:
- **Docker**: Set in `docker-compose.prod.yml` (`REDIS_HOST=redis`, `REDIS_PORT=6379`)
- **Local**: Set in `.env` (`REDIS_HOST=localhost`, `REDIS_PORT=6379`)

### S3 bucket region mismatch
The S3 bucket and EC2 instance can be in different regions. All deploy scripts
auto-detect the bucket's actual region via `s3api get-bucket-location`. If you see
presigned URL errors, verify:
```bash
aws s3api get-bucket-location --bucket fraud-demo-deploy-<ACCOUNT_ID>
```

### SSM command times out
The default SSM execution timeout is 3600 seconds. The deploy script polls 80 times
at 15-second intervals (~20 min). Check status manually:
```bash
aws ssm get-command-invocation \
  --command-id <COMMAND_ID> \
  --instance-id <INSTANCE_ID> \
  --region us-east-1
```

### SSM agent not registering
After `make deploy-aws`, wait ~2 minutes for cloud-init to complete. Verify:
```bash
aws ssm describe-instance-information \
  --region us-east-1 \
  --filters "Key=InstanceIds,Values=<INSTANCE_ID>" \
  --query "InstanceInformationList[0].PingStatus"
# Should return "Online"
```

### Instance info file
Deployment state is saved in `deploy/.instance-info`:
```
INSTANCE_ID=i-09d836295e39c159e
PUBLIC_IP=54.173.19.237
REGION=us-east-1
KEY_NAME=fraud-demo-key
```
All `make deploy-*` and `make ssm-*` commands read from this file automatically.

---

## 9. Quick Reference

### Local workflow
| Command | What it does |
|---------|-------------|
| `make infra-up` | Start local Postgres + Redis |
| `make seed-data` | Generate synthetic reference + transaction data |
| `make offline-pipeline` | Postgres → DuckDB → dbt → parquet → Redis |
| `make train` | Build training dataset + train model |
| `make push-artifacts` | Upload parquet + model + registry + DuckDB → S3 |

### AWS deployment
| Command | What it does |
|---------|-------------|
| `make deploy-aws` | Provision EC2 + IAM + security group |
| `make deploy-push` | Deploy code → S3 → SSM → pull artifacts → docker compose up |
| `make deploy-init` | One-time: seed EC2 Postgres (reference + historical data) |
| `make deploy-stop` | Stop instance (saves money) |
| `make deploy-start` | Restart stopped instance |
| `make deploy-terminate` | Delete everything permanently |

### SSM access
| Command | What it does |
|---------|-------------|
| `make ssm-setup` | One-time: attach SSM IAM role to instance |
| `make ssm-shell` | Interactive shell on EC2 (via SSM, no SSH) |
| `make ssm-tunnel` | Port-forward API (8000) to localhost |
| `make ssm-tunnel-mlflow` | Port-forward MLflow (5000) to localhost |
| `make ssm-tunnel-locust` | Port-forward Locust UI (8089) to localhost |

### Load testing (on EC2 — recommended)
| Command | What it does |
|---------|-------------|
| `make start-remote-locust` | Start Locust container on EC2 (with `LOCUST_SERVER_TIME=1`) |
| `make ssm-tunnel-locust` | Tunnel Locust UI → http://localhost:8089 |

### On EC2 (via SSM shell)
| Command | What it does |
|---------|-------------|
| `make stream-docker EPS=20` | Start simulator (20 txn/sec) in Docker |
| `make train-docker` | Run training pipeline in isolated Docker container |
| `make train-docker CONFIG=...` | Run training with specific config |
| `make docker-stats` | Live CPU/RAM per plane (serving vs training) |

