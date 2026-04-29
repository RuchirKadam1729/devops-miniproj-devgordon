# DevGordon — Local DevOps AI Agent

A conversational DevOps assistant running entirely on your machine. No cloud APIs. No data leaving your network.

**Type what you want done** → DevGordon explains what it will do → You approve → It executes.

**Stack:** FastAPI · Ollama (local LLM) · Jenkins · Ansible · Kubernetes · SonarQube · Docker

---

## TL;DR — Get Running in 5 Minutes

```bash
# 1. Start everything
docker compose up -d

# 2. Pull an LLM model (one time, ~2-5 min)
docker exec devgordon-ollama ollama pull llama3

# 3. Open the app
open http://localhost:8000

# 4. Try it
# Type: "what pods are running?" → DevGordon runs kubectl, shows results
# Type: "get me a list of Docker images" → DevGordon lists images
# Type: "trigger a Jenkins build" → DevGordon shows what it'll do, asks permission
```

Done. Everything works locally with no external APIs.

---

## What DevGordon Can Do

### Chat & Approval Workflow
- Type a request in plain English
- DevGordon explains what it will do
- Shows you the exact command/playbook before running it
- You click **Approve** or **Reject**
- If approved, it runs and shows results

### Infrastructure Tools
- **Kubernetes**: `kubectl get pods`, `kubectl describe`, `kubectl apply`, etc.
- **Ansible**: Generate and run playbooks for infra automation
- **Jenkins**: Trigger build jobs, check pipeline status
- **Docker**: List containers/images, view logs, check stats
- **Pre-execution scanning**: Ansible playbooks are linted before execution (catches security issues)

### Model Selection
- Automatically detects installed Ollama models
- Gracefully falls back if a model is deleted
- Switch models mid-session without restarting

### MCP (Model Context Protocol)
- Tools are exposed as standard MCP endpoints
- External LLM clients (Claude, other agents) can discover and call your tools
- JSON API: `/mcp/tools` (list tools) + `/mcp/call` (execute)

---

## Installation & Setup

### Prerequisites
```bash
# Required:
- Docker + Docker Compose
- Minikube running (for kubectl demos): minikube start

# Optional:
- Ansible/kubectl installed locally (not needed; they're in the container)
- Jenkins API token (only needed if triggering Jenkins jobs)
```

### Step 1: Start the Stack

```bash
git clone <your-repo>
cd devgordon

docker compose up -d
```

This starts:
- **DevGordon app** (FastAPI) on port 8000
- **Ollama** (local LLM) on port 11434
- **Jenkins** (CI/CD) on port 8080
- **SonarQube** (code quality) on port 9000

### Step 2: Pull an LLM Model (First Time Only)

```bash
# Pick a model from https://ollama.ai/library
docker exec devgordon-ollama ollama pull llama3

# Verify it's installed
curl http://localhost:8000/models | jq '.selected_model'
# Expected: "llama3:latest"
```

**Model is now cached.** All future restarts use it instantly — no re-downloading.

### Step 3: (Optional) Set Up Jenkins

If you want to **trigger Jenkins jobs** from DevGordon:

```bash
# Get initial admin password
docker exec devgordon-jenkins cat /var/jenkins_home/secrets/initialAdminPassword

# Open http://localhost:8080 and log in
# Install suggested plugins
# Create an admin user
# Go to: Manage Jenkins > Security > Create API token
# Copy the token
```

**Add token to `.env`:**
```bash
cat > .env << EOF
JENKINS_TOKEN=your_jenkins_api_token_here
EOF

# Restart the app to load the token
docker compose restart devgordon-app
```

### Step 4: (Optional) Set Up SonarQube

For code quality scanning in the Jenkins pipeline:

```bash
# Open http://localhost:9000
# Login: admin / admin  (change this!)

# Create a project named "devgordon"
# Generate a token, add to .env:
SONAR_TOKEN=your_sonarqube_token_here
```

---

## Using DevGordon

### The Web UI (Easiest)

1. **Open** http://localhost:8000
2. **Type** a request: `"what pods are running?"`, `"show me Docker images"`, `"get the last Jenkins build log"`
3. **Review** what DevGordon plans to do
4. **Click Approve** → It executes and shows results

### API Endpoints

All requests return JSON. No auth required (local only).

#### Chat (Conversational)
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "list kubernetes pods"}'
```

Response:
```json
{
  "type": "message",
  "content": "I'll run kubectl get pods to show you the running pods...",
  "model_used": "llama3:latest"
}
```

#### Model Management
```bash
# List available models
curl http://localhost:8000/models | jq '.models'

# Switch models
curl -X POST http://localhost:8000/select-model \
  -H "Content-Type: application/json" \
  -d '{"model": "mistral:latest"}'
```

#### Health Check
```bash
curl http://localhost:8000/health | jq '.'
# Shows: Ollama status, available models, Jenkins/SonarQube URLs
```

#### MCP Tools (For External Agents)
```bash
# Discover all tools
curl http://localhost:8000/mcp/tools | jq '.tools'

# Call a tool
curl -X POST http://localhost:8000/mcp/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "kubectl_command",
    "arguments": {"command": "get pods"}
  }'
```

---

## Model Caching & Management

### How Caching Works

**First pull:**
```bash
docker exec devgordon-ollama ollama pull llama3
# Downloads ~4GB, takes ~3 min, stores in /root/.ollama
```

**What happens next:**
- `/root/.ollama` is mounted to Docker volume `ollama_models`
- Volume persists on your host between container restarts
- Next `docker compose up`: model is already there, loads instantly

**No re-downloading on every restart.**

### Switching Models

```bash
# Pull multiple models
docker exec devgordon-ollama ollama pull mistral
docker exec devgordon-ollama ollama pull neural-chat

# Check what's installed
curl http://localhost:8000/models | jq '.models'
# Output: ["llama3:latest", "mistral:latest", "neural-chat:latest"]

# Switch to a different one
curl -X POST http://localhost:8000/select-model \
  -H "Content-Type: application/json" \
  -d '{"model": "mistral:latest"}'
```

Chat history is preserved when switching — no data loss.

### Available Models

From https://ollama.ai/library:
- `llama3` (8B, fast, good for local) ← **Recommended**
- `mistral` (7B, very fast, slightly lower quality)
- `neural-chat` (7B, specialized for chat)
- `llama2` (7B, older but stable)
- Larger models available but require more VRAM

---

## Running Locally (Dev Mode)

For development without Docker:

```bash
# Start Ollama separately
ollama serve  # In one terminal

# In another terminal:
cd app
pip install -r ../requirements.txt
pip install ansible ansible-lint uvicorn

# Optional: start ollama model pull
# ollama pull llama3

# Run the app
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000

Changes to `app/main.py` auto-reload. Perfect for development.

---

## Project Structure

```
devgordon/
├── app/
│   ├── main.py              # FastAPI app (routes, chat, execution)
│   ├── tools.py             # Tool definitions & executors
│   │                         # (kubectl, ansible, jenkins, docker, scanning)
│   ├── mcp_server.py        # MCP interface (optional, for external clients)
│   ├── scanner.py           # Pre-execution scanning (ansible-lint)
│   └── static/
│       └── index.html       # Web UI (chat interface)
│
├── k8s/
│   ├── deployment.yaml      # Kubernetes Deployment manifest
│   └── service.yaml         # Kubernetes Service (NodePort)
│
├── ansible/
│   ├── deploy.yml           # Playbook: deploy to K8s
│   └── inventory.ini        # Ansible inventory
│
├── Dockerfile               # Main app container
├── Dockerfile.ollama        # Ollama base (uses official image)
├── docker-compose.yml       # Full stack orchestration
├── Jenkinsfile              # CI/CD pipeline
├── requirements.txt         # Python dependencies
├── sonar-project.properties # SonarQube config
└── README.md                # This file
```

---

## Connecting to Your Minikube Cluster

DevGordon runs inside Docker but needs to reach **your** Minikube cluster on the host.

### The Problem
Minikube's kubeconfig points to `127.0.0.1:6443`. Inside a Docker container, `127.0.0.1` is the container itself, not your host.

### The Solution

**Option A: Docker Desktop / Lima / OrbStack (Easiest)**
```bash
# Your Minikube is already accessible at docker.host.internal:6443
# (Docker Desktop handles this automatically)
# No extra config needed!
```

**Option B: Native Minikube on Linux**
```bash
# Get your Docker bridge IP (usually 172.17.0.1)
ip addr show docker0 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1
# Output: 172.17.0.1

# Update kubeconfig to use this IP
sed -i 's/127.0.0.1/172.17.0.1/g' ~/.kube/config

# Restart DevGordon
docker compose restart devgordon-app

# Test
docker exec devgordon-app kubectl get pods
# Should show your actual pods
```

**Option C: Start Minikube with accessible API server**
```bash
minikube start --listen-address=0.0.0.0
# Now Minikube API is accessible from other machines too
```

---

## Demo Flow

### 30-Second Quick Demo

```bash
# 1. Open http://localhost:8000

# 2. Type: "what pods are running?"
# Expected: DevGordon runs kubectl get pods, shows results

# 3. Type: "trigger the devgordon-deploy Jenkins job"
# Expected: Shows approval card → Click Approve → Jenkins runs

# 4. Open http://localhost:8080 to see the build running

# 5. Open http://localhost:9000 to see SonarQube scan results
```

### Full Feature Demo (3 minutes)

1. **Ask DevGordon about infrastructure**
   - "How many pods are running?"
   - "Show me all Docker images"
   - "What's the status of the devgordon-deploy job?"

2. **Approve and execute**
   - "Deploy this Ansible playbook" (shows the playbook, waits for approval)
   - "Scale the deployment to 3 replicas" (DevGordon creates the kubectl command)
   - "Trigger a build" (Jenkins job runs)

3. **Show the security angle**
   - Upload an Ansible playbook with security issues
   - DevGordon's scanner (`ansible-lint`) flags them
   - You can approve anyway or ask DevGordon to fix it

---

## Technology Mapping (For Marking)

| Tech | How It's Used | Marks |
|---|---|---|
| **Jenkins** | CI/CD pipeline (Jenkinsfile) + DevGordon can trigger jobs | CI/CD 50% |
| **Ansible** | Infrastructure automation playbooks + DevGordon can run them | App 50% |
| **Docker** | Containerized app + full stack (compose) | App 50% |
| **Kubernetes** | Final deployment target + DevGordon inspects via kubectl | App 50% |
| **SonarQube** | Quality gate in pipeline + playbook scanning | Both |

All components are integrated and working together.

---

## Troubleshooting

### "Ollama is not reachable"
```bash
# Check if Ollama container is running
docker ps | grep ollama
# If not running:
docker compose up -d

# Check Ollama health
curl http://localhost:11434/api/tags
# Should return: {"models":[{"name":"llama3:latest",...}]}
```

### "No models installed"
```bash
# Pull a model
docker exec devgordon-ollama ollama pull llama3

# Verify
curl http://localhost:8000/models
```

### "kubectl: command not found"
```bash
# kubectl is inside the container, not your host. Use:
docker exec devgordon-app kubectl get pods
# Or let DevGordon do it via the UI
```

### "Jenkins 401 Unauthorized"
```bash
# Your JENKINS_TOKEN in .env is missing or wrong
# Regenerate it in Jenkins > Manage Jenkins > Security
# Update .env and restart:
docker compose restart devgordon-app
```

### "Can't reach Minikube from the container"
See "Connecting to Your Minikube Cluster" above. TL;DR: use `--listen-address=0.0.0.0` when starting Minikube.

### "Chat is slow"
- Models run on CPU by default (slow)
- If you have a GPU, uncomment the `deploy.resources.reservations` section in docker-compose.yml
- Smaller models (mistral, neural-chat) are faster than llama3

---

## API Reference

### `/` (GET)
Returns the web UI (HTML).

### `/health` (GET)
```json
{
  "status": "ok",
  "ollama": "ok",
  "models_available": ["llama3:latest"],
  "selected_model": "llama3:latest",
  "jenkins": "http://jenkins:8080",
  "sonarqube": "http://sonarqube:9000"
}
```

### `/models` (GET)
List available models and current selection.

### `/select-model` (POST)
Switch to a different model. Payload: `{"model": "mistral:latest"}`

### `/chat` (POST)
Send a message to the LLM. Payload: `{"message": "...", "history": []}`

### `/mcp/tools` (GET)
List all available tools (MCP format). For external agents.

### `/mcp/call` (POST)
Execute a tool. Payload: `{"tool": "tool_name", "arguments": {...}}`

### `/history` (GET)
Get conversation history for this session.

### `/history` (DELETE)
Clear conversation history.

### `/reset` (POST)
Same as DELETE /history.

---

## Environment Variables

Create `.env` in the project root:

```bash
# Jenkins API token (optional, only if triggering jobs)
JENKINS_TOKEN=your_token_here

# SonarQube token (optional, for pipeline scanning)
SONAR_TOKEN=your_token_here

# These default to localhost; change if running on different hosts
OLLAMA_URL=http://ollama:11434
JENKINS_URL=http://jenkins:8080
SONAR_URL=http://sonarqube:9000
```

Docker Compose automatically loads `.env`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                                                           │
│  Browser → http://localhost:8000                         │
│           (Web UI)                                        │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  FastAPI app (main.py)                                   │
│  ├─ Chat endpoint (/chat)                               │
│  │  └─ Calls Ollama (local LLM)                          │
│  ├─ Tool execution (/execute, /mcp/call)               │
│  │  └─ Calls: kubectl, ansible-playbook, docker, etc   │
│  └─ MCP interface (/mcp/tools, /mcp/call)              │
│     └─ For external agents (Claude, other LLMs)        │
│                                                           │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  Infrastructure (via Docker):                            │
│  ├─ Ollama (port 11434) — Local LLM                      │
│  ├─ Jenkins (port 8080) — CI/CD                          │
│  ├─ SonarQube (port 9000) — Code quality                 │
│  └─ Host machine                                         │
│     ├─ Minikube (port 6443) — K8s cluster               │
│     ├─ Docker daemon — For docker commands              │
│     └─ Ansible — Installed in container                 │
│                                                           │
└─────────────────────────────────────────────────────────┘
```

All communication is local. Nothing leaves your network.

---

## Development

### Adding a New Tool

1. Define the tool in `app/tools.py` (add to `TOOL_DEFINITIONS`)
2. Implement the executor function (e.g., `_my_tool(args)`)
3. Register it in `execute_tool()` dispatch table
4. Restart the app: `docker compose restart devgordon-app`

### Testing Locally

```bash
cd app
python3 -m pytest ../tests/  # If you add tests

# Manual test
python3 -c "from tools import execute_tool; print(execute_tool('kubectl_command', {'command': 'get pods'}))"
```

### Building & Pushing

```bash
# Build custom images
docker compose build

# Tag for registry
docker tag devgordon-app:latest myregistry/devgordon-app:v1.0
docker push myregistry/devgordon-app:v1.0
```

---

## Known Limitations

- **Single session**: Conversation history is in-memory (cleared on restart)
- **No auth**: Designed for local use only
- **CPU inference**: Models run on CPU by default (slow; GPU optional)
- **Minikube only**: Tested with Minikube, may need adjustments for other K8s distros
- **Linux/Mac**: Tested on Linux and macOS; Windows users may need WSL2 adjustments

---

## License

No license specified. Use as needed for your lab.

---

## Support / Questions

- Check `/health` endpoint to see if all services are running
- View logs: `docker compose logs -f devgordon-app`
- Restart stack: `docker compose down && docker compose up -d`
- Check model: `curl http://localhost:8000/models | jq`

---

**DevGordon: Your local, private, conversational DevOps buddy.** No cloud. No data loss. Just you, your infrastructure, and an AI that asks before it executes.
