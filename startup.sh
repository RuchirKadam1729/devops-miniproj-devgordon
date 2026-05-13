#!/bin/bash
# startup.sh
# Complete automated DevGordon startup with Groq API testing
# Run this once when you wake up — everything will be ready

set -e

echo "╔════════════════════════════════════════════════════════════════════════════════╗"
echo "║        DevGordon Automated Startup with Groq API (No Ollama Required)          ║"
echo "║                  Ansible runs in Docker — no external dependencies              ║"
echo "╚════════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Load Groq API key
export GROQ_API_KEY=$(cat ./secrets/GROQ_API_KEY)
echo "✓ Groq API key loaded"

# Cleanup
echo ""
echo "[Step 1/4] Cleaning up previous runs..."
docker compose -f docker-ansible-compose.yml down --remove-orphans 2>/dev/null || true
rm -f test_results/test_run_groq.txt
sleep 2

# Start stack
echo "[Step 2/4] Starting DevGordon stack (app, Jenkins, SonarQube)..."
docker compose -f docker-ansible-compose.yml up -d app jenkins sonarqube
sleep 5

# Wait for health
echo "[Step 3/4] Waiting for app to be healthy..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✓ App healthy"
        break
    fi
    echo "  Waiting... ($i/30)"
    sleep 1
done

# Run tests
echo "[Step 4/4] Running comprehensive test suite (9 stages)..."
echo ""

export GROQ_API_KEY=$(cat ./secrets/GROQ_API_KEY)
docker compose -f docker-ansible-compose.yml run --rm ansible-runner

echo ""
echo "════════════════════════════════════════════════════════════════════════════════"
echo "✓ STARTUP COMPLETE"
echo "════════════════════════════════════════════════════════════════════════════════"
echo ""
echo "DevGordon is running at: http://localhost:8000"
echo "Test results: ./test_results/test_run_groq.txt"
echo ""
echo "Logs (live):"
echo "  docker compose -f docker-ansible-compose.yml logs -f app"
echo ""
echo "Stop all services:"
echo "  docker compose -f docker-ansible-compose.yml down"
echo ""
