# DevGordon — Local DevOps Agent

Conversational DevOps assistant. Type what you want done in plain English → DevGordon proposes the exact command → you approve or reject → it executes and interprets the output. No cloud APIs. Everything runs on your machine.

**Stack:** FastAPI · Ollama (local LLM, runs on host for GPU) · Jenkins · Ansible · Kubernetes · SonarQube · Docker

---

## Quick Start

```bash
# 1. Install Ollama on your host (not in Docker — needed for GPU access)
#    https://ollama.com/download
ollama pull qwen3:8b

# 2. Start the stack
docker compose up -d

# 3. Open the UI
open http://localhost:8000
```

That's it. The app connects to Ollama running on your host via `host.docker.internal:11434`.

---

## Why Ollama runs on the host, not in Docker

Metal GPU passthrough doesn't exist for containers on macOS. If Ollama runs inside Docker it gets CPU-only inference (~60s per response, timeouts). On the host it hits the GPU directly via Metal and runs in 2–5s. The app reaches it via `host.docker.internal:11434` which Docker Desktop routes automatically.

To use a different model, pull it with Ollama and set `OLLAMA_MODEL` in your `.env`:

```bash
ollama pull llama3.1:8b
echo "OLLAMA_MODEL=llama3.1:8b" >> .env
docker compose restart app
```

---

## Kubernetes Setup

DevGordon runs kubectl inside the app container, so it needs a kubeconfig that points to somewhere reachable from inside Docker — not `127.0.0.1` (which is the container itself).

The Dockerfile handles this automatically at startup: it copies your kubeconfig to `/tmp/kube/config` and rewrites `127.0.0.1` → `host.docker.internal`, then sets `KUBECONFIG` to point there. Your original `~/.kube/config` is mounted read-only and never modified.

**Option A — Docker Desktop built-in Kubernetes (recommended)**

Enable it in Docker Desktop → Settings → Kubernetes → Enable Kubernetes. No minikube needed. The kubeconfig it generates already uses `kubernetes.docker.internal` so the rewrite is a no-op.

**Option B — Minikube**

```bash
minikube start
# The Dockerfile startup script handles the 127.0.0.1 → host.docker.internal rewrite
```

**Make it work without any K8s**

If you don't need kubectl at all, just ignore it. The rest of DevGordon (Jenkins, Docker, Ansible, chat) works fine without a cluster. Set `KUBECONFIG_AVAILABLE=false` in `.env` to suppress kubectl errors.

---

## Approval Modes

The header has a 3-way toggle that controls when DevGordon asks for confirmation:

| Mode | Behaviour |
|------|-----------|
| **Ask always** | Approval card before every tool call (default, safest) |
| **Writes only** | Auto-runs reads (`kubectl get`, `docker ps`, `jenkins status`), asks for writes |
| **Auto-run** | Executes everything without asking |

After execution, results always show as a single message bubble with two expandable sections: the command that ran and the raw output.

---

## Configuration

Copy `.env.example` to `.env` and edit:

```bash
# Ollama (host, not Docker)
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3:8b

# Jenkins (optional — only needed to trigger builds)
JENKINS_URL=http://jenkins:8080
JENKINS_TOKEN=your_token_here

# SonarQube (optional — only needed for full Python scanning)
SONAR_URL=http://sonarqube:9000
SONAR_TOKEN=your_token_here
```

---

## Services

| Service | Port | Purpose |
|---------|------|---------|
| DevGordon UI | 8000 | Chat interface + FastAPI backend |
| Jenkins | 8080 | CI/CD — trigger builds, view logs |
| SonarQube | 9000 | Code quality — scans Ansible playbooks pre-execution |
| Ollama | 11434 | LLM inference (runs on **host**, not in compose) |

Jenkins and SonarQube start automatically with `docker compose up -d`. First-time Jenkins setup:

```bash
# Get the initial admin password
docker exec devgordon-jenkins cat /var/jenkins_home/secrets/initialAdminPassword
# Open http://localhost:8080, install suggested plugins, create user, generate API token
# Add JENKINS_TOKEN to .env and restart: docker compose restart app
```

---

## What DevGordon Can Do

**Kubernetes** — pod status, logs, deployments, scaling, applying manifests. Reads auto-run in "Writes only" mode; destructive ops always ask.

**Docker** — container listing, image management, logs, stats.

**Ansible** — generates playbooks, scans them with ansible-lint before showing the approval card, then runs them. Security issues (0777 perms, http:// URLs, shell injection) are flagged.

**Jenkins** — trigger jobs, check build status, view console logs.

**General chat** — if no tool is needed DevGordon just answers directly.

---

## Pre-execution Scanning

Before every approval card, DevGordon runs a security scan on the proposed action:

- **Ansible playbooks**: ansible-lint + manual pattern checks (world-writable perms, unencrypted URLs, shell injection risk, secrets without `no_log`)
- **kubectl**: flags `delete`, `--force`, `--all`, namespace deletes
- **Docker**: flags `system prune`

The scan result is shown in the approval card. Critical issues turn the card red; you can still approve but you've been warned.

---

## API

```
GET  /             → Web UI
GET  /health       → Ollama + service status
POST /chat         → Send message, get response or approval card
POST /execute      → Approve and run a pending tool call
POST /reject       → Reject a pending tool call
GET  /history      → Conversation history
POST /reset        → Clear history
GET  /approval-mode      → Get current approval mode
POST /approval-mode      → Set mode: "always" | "writes" | "never"
GET  /mcp/tools    → Tool discovery (MCP format, for external agents)
POST /mcp/call     → Execute a tool directly (MCP)
POST /scan-code    → Scan code without executing
GET  /sonarqube-standards → Recent SonarQube project issues
```

---

## Project Structure

```
devgordon/
├── app/
│   ├── main.py           # FastAPI backend, all routes, approval modes
│   ├── tools.py          # Tool definitions + executors (kubectl, ansible, jenkins, docker)
│   ├── scanner.py        # Pre-execution security scanning
│   ├── mcp_server.py     # MCP interface for external agents
│   └── static/
│       └── index.html    # Chat UI with mode toggle + execution bubbles
├── ansible/
│   ├── deploy.yml        # Playbook: deploy to K8s
│   └── inventory.ini     # Ansible inventory
├── k8s/
│   ├── deployment.yaml   # K8s Deployment manifest
│   └── service.yaml      # K8s Service (NodePort)
├── tests/
│   └── test_devgordon.py # Test suite
├── scripts/
│   └── pull-ollama-model.sh
├── Dockerfile
├── docker-compose.yml
├── Jenkinsfile
├── sonar-project.properties
└── requirements.txt
```

---

## Troubleshooting

**"Cannot connect to Ollama at host.docker.internal:11434"**
Ollama isn't running on the host. Run `ollama serve` or start the Ollama app, then try again.

**"model_not_found"**
The model in `OLLAMA_MODEL` isn't pulled yet. Run `ollama pull qwen3:8b`.

**kubectl errors / connection refused**
Either no cluster is running, or the kubeconfig rewrite didn't work. Check `docker logs devgordon-app` for the startup script output. Enable Docker Desktop Kubernetes for the easiest path.

**Slow responses**
Ollama is probably running on CPU. Check Ollama logs — look for `offloaded X/37 layers to GPU`. If it says 0, Ollama isn't using the GPU. On Apple Silicon, just running Ollama natively (not in Docker) fixes this automatically.

**Jenkins 401**
`JENKINS_TOKEN` is missing or wrong. Regenerate it in Jenkins → Manage Jenkins → Security → Create API Token, update `.env`, restart the app.
