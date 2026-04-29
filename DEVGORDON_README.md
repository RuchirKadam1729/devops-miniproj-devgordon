# DevGordon — Local DevOps Agent

A fully local DevOps assistant powered by Ollama (qwen3:8b) with tool calling for Kubernetes, Docker, Ansible, and Jenkins.

## Quick Start

### 1. Start the stack

```bash
docker compose up -d
```

All services start: FastAPI app, Ollama, Jenkins, SonarQube.

### 2. Pull the Ollama model (one time only)

```bash
./scripts/pull-ollama-model.sh qwen3:8b
```

This downloads qwen3:8b (~5.2 GB) and caches it in the `ollama_models` Docker volume. Subsequent runs load it instantly.

### 3. Open the web UI

```bash
open http://localhost:8000
```

## Configuration

Edit `.env` to customize:
- `OLLAMA_URL`: Where Ollama is accessible (default: `http://ollama:11434`)
- `OLLAMA_MODEL`: Which model to use (default: `qwen3:8b`)
- `JENKINS_URL`, `JENKINS_TOKEN`: CI/CD integration
- `SONAR_URL`, `SONAR_TOKEN`: Code quality scanning

## Features

- **Local LLM**: qwen3:8b with native tool calling (no external APIs)
- **Approval Workflow**: LLM suggests tools, shows approval card before execution
- **Security Scanning**: Pre-execution code scan via ansible-lint + custom rules
- **Infrastructure Integration**: Kubernetes, Docker, Ansible, Jenkins
- **Persistent Storage**: Models cached in Docker volumes for instant restarts

## Available Commands

Try these in the UI:

- "what pods are running?" → `kubectl get pods`
- "trigger the build job" → `jenkins build`
- "show recent docker containers" → `docker ps -a`
- "deploy an Ansible playbook" → Generates playbook, scans, approves, executes

## Development

### Run Tests

```bash
pytest tests/test_devgordon.py -v
```

50+ tests covering health checks, model management, chat, scanning, and integration.

### Logs

```bash
docker compose logs -f app       # FastAPI backend
docker compose logs -f ollama    # Ollama LLM
docker compose logs -f jenkins   # Jenkins CI/CD
```

### Stop Everything

```bash
docker compose down
```

Models persist in the `ollama_models` volume, so restarting is instant.

## Switch Models

To use a different model (e.g., llama3.1:8b):

```bash
./scripts/pull-ollama-model.sh llama3.1:8b
```

Or via the web UI: Settings → Select Model → choose from available models.

## Troubleshooting

**Model pull failing?**
```bash
docker exec devgordon-ollama ollama pull qwen3:8b
```

**Check Ollama is healthy:**
```bash
docker compose logs ollama
```

**Verify model is loaded:**
```bash
docker exec devgordon-ollama ollama list
```

**App can't reach Ollama?**
```bash
docker exec devgordon-app curl http://ollama:11434/api/tags
```

## Architecture

```
User Request
    ↓
FastAPI /chat endpoint
    ↓
Ollama qwen3:8b (with tool calling)
    ↓
[LLM decides: call tool or respond]
    ↓
pre_scan (ansible-lint + custom rules)
    ↓
Approval card shown to user
    ↓
User approves or rejects
    ↓
execute_tool (kubectl, ansible, jenkins, docker)
    ↓
Ollama interprets results
    ↓
Response to user
```

## Files

- `app/main.py` — FastAPI backend with Ollama integration
- `app/tools.py` — Tool definitions (kubectl, ansible, jenkins, docker)
- `app/scanner.py` — Pre-execution security scanning
- `app/static/index.html` — Web UI
- `Dockerfile` — App container
- `Dockerfile.ollama` — Ollama container
- `docker-compose.yml` — Orchestration
- `.env.example` → `.env` — Configuration
- `scripts/pull-ollama-model.sh` — Model download script
- `tests/test_devgordon.py` — 50+ tests

## Why Local?

- No API keys or authentication required
- No usage limits or costs
- Full control over execution
- Runs offline after initial model download
- Models persist in Docker volumes for instant restarts

## Next Steps

1. Explore the web UI — ask it questions about your infrastructure
2. Try triggering a Jenkins build or ansible playbook
3. Add your own tools in `app/tools.py`
4. Customize scanning rules in `app/scanner.py`
5. Deploy to your lab environment with `docker compose up -d`
