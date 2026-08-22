# Deploying the demo to AWS

The container is self-contained: `DELTA_DEMO_FREEZE=1` means it makes **no
outbound calls**. The bundled SQLite database carries all 6,276 filings, 53
events, 124 documents and the warm response cache, so it behaves identically on
AWS to your laptop — no NSE, no SEBI, no Gemini needed to serve the demo.

Verified locally: image builds, container reports healthy, all routes 200,
slowest page 0.67s.

---

## Step 1 — you do this (I can't authenticate as you)

Sign in at the console URL, then create an access key:
**IAM → Users → your user → Security credentials → Create access key →
Command Line Interface (CLI)**

Install and configure the CLI:

```bash
brew install awscli
aws configure    # paste Access key ID + Secret, region: ap-south-1, output: json
aws sts get-caller-identity      # should print your account id
```

Tell me when that returns your identity and I'll run everything below.

---

## Step 2 — App Runner (recommended)

Fully managed, gives you an HTTPS URL, no servers to babysit. ~10 minutes.

```bash
ACCOUNT=335019467867
REGION=ap-south-1
REPO=delta-filings

aws ecr create-repository --repository-name $REPO --region $REGION
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

# Build for AWS's architecture — Apple Silicon defaults to arm64 and
# App Runner expects x86_64.
docker buildx build --platform linux/amd64 \
  -t $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:demo --push .
```

Then create the service (console is quicker than the CLI here):
**App Runner → Create service → Container registry → Amazon ECR →** pick the
image → **Port 8080** → 1 vCPU / 2 GB → Create.

You get `https://<id>.ap-south-1.awsapprunner.com` — that is your submission link.

---

## Step 3 — EC2 fallback (if App Runner is not enabled on the account)

```bash
# t3.small, Amazon Linux 2023, security group open on 80
# after SSH-ing in:
sudo dnf install -y docker && sudo systemctl start docker
aws ecr get-login-password --region ap-south-1 | sudo docker login --username AWS \
  --password-stdin 335019467867.dkr.ecr.ap-south-1.amazonaws.com
sudo docker run -d --restart always -p 80:8080 \
  335019467867.dkr.ecr.ap-south-1.amazonaws.com/delta-filings:demo
```

---

## Before you submit the link

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://<your-url>/demo
```

Then click through: Signal → an event → Approve → Copilot. If any page is slow,
the cache did not ship — check `delta_filings.db` is inside the image
(`docker run --rm <image> ls -la delta_filings.db`).

---

## Judge-facing notes

- **Entry point is `/demo`** — no sign-in, no payment wall.
- **Frozen by design.** The container serves from a snapshot taken while the
  market was open. That is a deliberate demo choice, not a limitation: set
  `DELTA_DEMO_FREEZE=0` and it fetches live.
- **AWS usage is honest.** This is App Runner + ECR hosting a self-contained
  container. Do not claim S3/CloudWatch/Bedrock involvement — none is used.
