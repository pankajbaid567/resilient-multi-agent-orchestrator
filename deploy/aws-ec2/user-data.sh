#!/bin/bash
# EC2 bootstrap for the backend (Amazon Linux 2023, arm64). Placeholders __HOST__ and
# __CORS__ are filled in at launch. No secrets: Bedrock access comes from the instance role.
set -euxo pipefail

# 1 GB instance: add swap so the image build and the app fit.
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048 && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
fi

dnf install -y docker git
systemctl enable --now docker

rm -rf /opt/app && git clone --depth 1 https://github.com/pankajbaid567/resilient-multi-agent-orchestrator.git /opt/app
docker build -t reliable-agent-backend /opt/app/backend

mkdir -p /opt/data /opt/caddy
cat > /opt/backend.env <<ENV
BEDROCK_ROLE_KEY_REGION=ap-south-1
OPEN_SOURCE_BASE_URL=https://bedrock-runtime.ap-south-1.amazonaws.com/openai/v1
PRIMARY_MODEL=openai.gpt-oss-120b-1:0
FALLBACK_MODEL=qwen.qwen3-32b-v1:0
VALIDATION_MODEL=openai.gpt-oss-20b-1:0
MODEL_CHAIN=openai.gpt-oss-120b-1:0,qwen.qwen3-32b-v1:0
MODEL_PRICES={"openai.gpt-oss-120b-1:0": [0.18, 0.71], "qwen.qwen3-32b-v1:0": [0.18, 0.71], "openai.gpt-oss-20b-1:0": [0.08, 0.35]}
LLM_REASONING_EFFORT=low
PARALLEL_MODE=true
MAX_CONCURRENT_STEPS=4
TASKS_PER_IP_PER_HOUR=5
DAILY_TASK_LIMIT=50
TASK_TOKEN_BUDGET=60000
TRUST_PROXY_HEADERS=true
CHECKPOINT_URL=sqlite:////data/checkpoints.db
REDIS_URL=redis://127.0.0.1:1
CORS_ORIGINS=__CORS__
ENABLE_CODE_EXEC=false
ENABLE_API_CALL=false
LOG_LEVEL=INFO
ENV

docker rm -f backend caddy || true
# Host networking: the container reaches the instance role through IMDSv2 while the hop
# limit stays at 1, and only Caddy (80/443) is exposed by the security group.
docker run -d --name backend --restart unless-stopped --network host \
  --env-file /opt/backend.env -v /opt/data:/data \
  reliable-agent-backend uvicorn main:app --host 127.0.0.1 --port 8000 --proxy-headers

cat > /opt/caddy/Caddyfile <<CADDY
__HOST__ {
  encode gzip
  reverse_proxy 127.0.0.1:8000
  header {
    Strict-Transport-Security "max-age=31536000"
    X-Content-Type-Options "nosniff"
    X-Frame-Options "DENY"
  }
}
CADDY
docker run -d --name caddy --restart unless-stopped --network host \
  -v /opt/caddy/Caddyfile:/etc/caddy/Caddyfile -v /opt/caddy/data:/data caddy:2.10.0
