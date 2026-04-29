#!/bin/bash
# DevGordon Quick Reference
# Copy-paste commands for common tasks

# ── STARTUP ──────────────────────────────────────────────────────────────────

# First time
ollama pull qwen3:8b
docker compose up -d
open http://localhost:8000

# Restart after changes
docker compose restart app

# ── STATUS ───────────────────────────────────────────────────────────────────

curl http://localhost:8000/health | jq '.'
docker compose logs -f app       # app logs
docker compose logs -f jenkins   # jenkins logs

# ── OLLAMA (runs on host, not in Docker) ─────────────────────────────────────

ollama list                      # what models are installed
ollama pull llama3.1:8b          # pull a different model
ollama ps                        # check if a model is loaded + GPU usage

# Switch model (edit .env, restart)
echo "OLLAMA_MODEL=llama3.1:8b" >> .env
docker compose restart app

# ── KUBERNETES ───────────────────────────────────────────────────────────────

# Option A: Docker Desktop (recommended)
# Enable in Docker Desktop → Settings → Kubernetes → Enable Kubernetes
# Then restart the app — kubeconfig is auto-patched at startup

# Option B: Minikube
minikube start
docker compose restart app       # triggers kubeconfig rewrite to host.docker.internal

# Verify kubectl works from inside the container
docker exec devgordon-app kubectl get pods

# ── JENKINS SETUP (optional) ──────────────────────────────────────────────────

# Get initial admin password
docker exec devgordon-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
# Open http://localhost:8080 → install plugins → create user → generate API token
# Then:
echo "JENKINS_TOKEN=your_token_here" >> .env
docker compose restart app

# ── API EXAMPLES ─────────────────────────────────────────────────────────────

# Chat
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "what pods are running?"}'

# Set approval mode
curl -X POST http://localhost:8000/approval-mode \
  -H 'Content-Type: application/json' \
  -d '{"mode": "writes"}'   # "always" | "writes" | "never"

# Scan code before execution
curl -X POST http://localhost:8000/scan-code \
  -H 'Content-Type: application/json' \
  -d '{"tool_name": "run_ansible_playbook", "tool_args": {"playbook_content": "---\n- hosts: localhost\n  tasks:\n    - file:\n        path: /tmp/x\n        mode: 0777\n"}}'

# MCP tool discovery (for external agents like Claude)
curl http://localhost:8000/mcp/tools | jq '[.tools[].name]'

# Clear conversation history
curl -X POST http://localhost:8000/reset

# ── STOPPING ─────────────────────────────────────────────────────────────────

docker compose down              # stop, keep volumes (Jenkins data, etc.)
docker compose down -v           # stop + wipe volumes (full reset)
