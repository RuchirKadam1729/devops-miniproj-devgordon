#!/bin/bash
# DevGordon setup script
# Guides a new user through environment configuration and pre-flight checks.

set -e
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

ok()   { echo -e "${GREEN}✔${RESET} $1"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $1"; }
fail() { echo -e "${RED}✗${RESET} $1"; }
hdr()  { echo -e "\n${BOLD}$1${RESET}"; }

echo -e "${BOLD}DevGordon — first-time setup${RESET}"
echo "────────────────────────────────────"

# ── 1. .env ──────────────────────────────────────────────────────────────────
hdr "1. Environment file"

if [ -f .env ]; then
  ok ".env already exists — skipping copy"
else
  if [ -f .env.example ]; then
    cp .env.example .env
    ok "Copied .env.example → .env"
  else
    cat > .env << 'EOF'
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3:8b
JENKINS_URL=http://jenkins:8080
JENKINS_TOKEN=
SONAR_URL=http://sonarqube:9000
SONAR_TOKEN=
EOF
    ok "Created .env with defaults"
  fi
fi

# ── 2. Ollama ─────────────────────────────────────────────────────────────────
hdr "2. Ollama"

if command -v ollama &>/dev/null; then
  ok "ollama binary found"
else
  fail "ollama not found — install from https://ollama.com/download and re-run this script"
  exit 1
fi

if ollama list &>/dev/null; then
  ok "Ollama is running"
else
  warn "Ollama doesn't appear to be running. Start it with: ollama serve"
fi

MODEL=$(grep OLLAMA_MODEL .env | cut -d= -f2 | tr -d ' ')
MODEL=${MODEL:-qwen3:8b}

if ollama list 2>/dev/null | grep -q "^${MODEL}"; then
  ok "Model '${MODEL}' is already pulled"
else
  echo "  Model '${MODEL}' not found locally. Pulling now (this may take a few minutes)..."
  ollama pull "$MODEL" && ok "Pulled ${MODEL}" || warn "Pull failed — you can run: ollama pull ${MODEL}"
fi

# ── 3. Docker ─────────────────────────────────────────────────────────────────
hdr "3. Docker"

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  ok "Docker is running"
else
  fail "Docker is not running. Start Docker Desktop and re-run this script."
  exit 1
fi

# ── 4. Kubernetes ─────────────────────────────────────────────────────────────
hdr "4. Kubernetes"

KUBECONFIG_PATH="${KUBECONFIG:-$HOME/.kube/config}"

if [ -f "$KUBECONFIG_PATH" ]; then
  ok "kubeconfig found at $KUBECONFIG_PATH"
  ok "It will be mounted read-only and patched at container startup"
else
  warn "No kubeconfig found at $KUBECONFIG_PATH"
  warn "kubectl tools won't work inside DevGordon until you set up a cluster"
  warn "Easiest option: Docker Desktop → Settings → Kubernetes → Enable Kubernetes"
fi

# ── 5. Optional tokens ────────────────────────────────────────────────────────
hdr "5. Optional integrations"

JENKINS_TOKEN=$(grep JENKINS_TOKEN .env | cut -d= -f2 | tr -d ' ')
if [ -z "$JENKINS_TOKEN" ]; then
  warn "JENKINS_TOKEN not set in .env — Jenkins tools will be unavailable"
  echo "  To set up: docker compose up -d jenkins"
  echo "             docker exec devgordon-jenkins cat /var/jenkins_home/secrets/initialAdminPassword"
  echo "  Then: http://localhost:8080 → create user → generate API token → add to .env"
else
  ok "JENKINS_TOKEN is set"
fi

SONAR_TOKEN=$(grep SONAR_TOKEN .env | cut -d= -f2 | tr -d ' ')
if [ -z "$SONAR_TOKEN" ]; then
  warn "SONAR_TOKEN not set in .env — SonarQube scanning will be limited to ansible-lint"
else
  ok "SONAR_TOKEN is set"
fi

# ── 6. Done ───────────────────────────────────────────────────────────────────
hdr "Setup complete"
echo ""
echo "  Start the stack:  docker compose up -d"
echo "  Open the UI:      open http://localhost:8000"
echo "  Health check:     curl http://localhost:8000/health | jq"
echo ""