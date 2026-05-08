# AWS Account Setup Plan — Fraud ML Demo

## 1. Create AWS Account (Free Tier + Credits)

### About the Free Credit
- AWS offers **$300 free credits** (not $30) via the **AWS Activate** program for startups, but for **individual new accounts** you get:
  - **12 months Free Tier** (includes t2.micro/t3.micro EC2, 750 hrs/month)
  - Some AWS events/promotions give **$25-$50 credits** — check https://aws.amazon.com/free/
  - If you're a student: **AWS Educate** gives $100 credits
  - GCP gives $300 free credit; Azure gives $200 — AWS is less generous for individuals

### Cost Estimate for This Demo
| Resource | Instance | Cost/hour | For 4-hour demo |
|----------|----------|-----------|-----------------|
| EC2 c5.2xlarge | 8 vCPU, 16GB | $0.34/hr | $1.36 |
| EC2 t3.medium (cheaper option) | 2 vCPU, 4GB | $0.0416/hr | $0.17 |
| EBS 30GB gp3 | Storage | $0.08/GB/mo | ~$0.01 |
| Data transfer | Outbound | ~free | negligible |

**Total for a 4-hour demo: ~$1.50 (c5.2xlarge) or ~$0.20 (t3.medium)**

> TIP: Use `t3.xlarge` ($0.1664/hr, 4 vCPU, 16GB) as a good middle ground.

---

## 2. Step-by-Step Account Creation

### 2a. Sign Up
1. Go to https://aws.amazon.com/free/
2. Click **"Create a Free Account"**
3. Enter email + password + account name
4. Choose **Personal** account type
5. Enter credit card (required but won't be charged under Free Tier limits)
6. Complete phone verification
7. Select **Basic Support** (free)

### 2b. Secure Your Account (5 min)
1. Log in to AWS Console → **IAM** service
2. Click your account name (top-right) → **Security credentials**
3. Enable **MFA** (use Google Authenticator or Authy)
4. Create an **IAM user** for CLI access:
   ```
   IAM → Users → Create User
   - Username: fraud-demo-admin
   - Attach policy: AdministratorAccess (for demo; narrow later)
   - Create access key → select "CLI" use case
   - Download the CSV (store securely!)
   ```

### 2c. Install & Configure AWS CLI
```bash
# Install AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Configure
aws configure
# AWS Access Key ID: <from step 2b>
# AWS Secret Access Key: <from step 2b>
# Default region: ap-southeast-2  (Sydney — closest to AU)
# Default output format: json

# Verify
aws sts get-caller-identity
```

---

## 3. Deploy the Demo

Once AWS CLI is configured, deploy in 3 commands:

```bash
cd fraud-realtime-ml-prototype

# 1. Provision EC2 instance (~2 minutes)
make deploy-aws REGION=ap-southeast-2

# 2. Push code & start services (~3 minutes)
make deploy-push

# 3. Run load test from your laptop
make load-test API_HOST=http://<PUBLIC_IP>:8000 USERS=500
```

### Expected Results (c5.2xlarge)
- **RPS**: 800-1500+
- **P50 latency**: 5-30ms
- **P95 latency**: 30-80ms
- **P99 latency**: 50-150ms

---

## 4. After the Demo — STOP THE INSTANCE!

```bash
# Stop (keeps data, stops charges for compute)
make deploy-stop

# Or terminate permanently (deletes everything)
make deploy-terminate
```

**IMPORTANT**: If you forget to stop, a c5.2xlarge costs ~$245/month!

---

## 5. Cost Management Tips

1. **Set a billing alarm**:
   - Console → Billing → Budgets → Create budget
   - Set $5/month alert (email notification)

2. **Use Spot instances** for testing (60-90% cheaper):
   - Modify `deploy-aws.sh`: add `--instance-market-options '{"MarketType":"spot"}'`
   - Risk: instance can be reclaimed (fine for demos)

3. **Region matters**:
   - `ap-southeast-1` (Singapore): cheapest in APAC
   - `us-east-1` (Virginia): cheapest overall
   - `ap-southeast-2` (Sydney): best latency from AU

4. **Free Tier alternatives** (if you just need to show it works):
   - `t3.micro` (2 vCPU, 1GB) — 750 hrs/month FREE for 12 months
   - Won't hit 500 RPS but will demonstrate the architecture
   - Latency will be ~50-100ms (still proves the system works)

---

## 6. Alternative: Use Free Tier Only ($0 Cost)

If you want zero cost for the demo:

```bash
# In deploy/deploy-aws.sh, change INSTANCE_TYPE:
INSTANCE_TYPE="t3.micro"  # Free tier eligible
```

Limitations on t3.micro:
- 1GB RAM — may OOM with 4 workers. Use `API_WORKERS=1`
- 2 vCPU — expect ~50-100 RPS
- Still shows < 80ms latency since server isn't contended

**Recommended for the demo**: Use `t3.xlarge` for 1-2 hours, total cost < $0.50.

---

## Quick Reference

| Command | What it does |
|---------|-------------|
| `make deploy-aws` | Provision EC2 + security group |
| `make deploy-push` | rsync + docker compose up |
| `make deploy-stop` | Stop instance (saves money) |
| `make deploy-start` | Restart stopped instance |
| `make deploy-terminate` | Delete everything permanently |
| `make deploy-local` | Test production stack locally |
| `make load-test API_HOST=http://IP:8000` | Load test against cloud |
