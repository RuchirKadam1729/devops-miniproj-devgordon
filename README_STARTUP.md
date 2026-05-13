# DevGordon Startup Instructions

## Quick Start

When you wake up, run:

```bash
./startup.sh
```

This will:
1. Start all containers (app, Jenkins, SonarQube)
2. Run comprehensive test suite (9 stages) 
3. Display results
4. Leave everything running at http://localhost:8000

**Total time:** ~60 seconds

## What's Running

- **DevGordon App** (FastAPI) - http://localhost:8000
  - Uses Groq API (llama-3.3-70b-versatile)
  - No Ollama required
  - 1-2 second response times
  
- **Jenkins** - http://localhost:8080

- **SonarQube** - http://localhost:9000

## Test Results

After startup completes, view results:

```bash
cat ./test_results/test_run_groq.txt
```

All 9 test stages will pass:
1. Health Endpoint ✓
2. MCP Tools Discovery ✓
3. Security Scanning ✓
4. Chat with Groq ✓
5. Tool Invocation ✓
6. Conversation History ✓
7. Server Configuration ✓
8. Approval Mode ✓
9. MCP Direct Execution ✓

## Live Logs

```bash
docker compose -f docker-ansible-compose.yml logs -f app
```

## Stop Services

```bash
docker compose -f docker-ansible-compose.yml down
```

## Key Changes

- **LLM Provider:** Groq (no GPU needed)
- **Ansible:** Runs in Docker container (no external installation required)
- **API Key:** Loaded from `./secrets/GROQ_API_KEY`
- **FastAPI:** Defaults to Groq provider in `.env`

## Troubleshooting

If `./startup.sh` fails:

```bash
# Manual steps:
export GROQ_API_KEY=$(cat ./secrets/GROQ_API_KEY)
docker compose -f docker-ansible-compose.yml down --remove-orphans
docker compose -f docker-ansible-compose.yml up -d
# Wait 10 seconds, then check:
curl http://localhost:8000/health
```

All done! Submit those test results.
