#!/bin/bash
# run_test_with_groq.sh
# Complete automated test run using Groq API (no Ollama needed)
# Runs inside Docker containers on macOS

set -e

echo "╔════════════════════════════════════════════════════════════════════════════════╗"
echo "║ DevGordon Automated Test Run with Groq API (Ansible in Docker)               ║"
echo "║ No external Ansible required — runs completely in containers               ║"
echo "╚════════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Check if Groq API key exists
if [ ! -f "./secrets/GROQ_API_KEY" ]; then
    echo "❌ ERROR: Groq API key not found at ./secrets/GROQ_API_KEY"
    exit 1
fi

GROQ_KEY=$(cat ./secrets/GROQ_API_KEY)
echo "✓ Groq API key loaded"
echo ""

# Cleanup any previous runs
echo "[1/5] Cleaning up previous containers..."
docker compose -f docker-ansible-compose.yml down --remove-orphans 2>/dev/null || true
sleep 2

# Start services
echo "[2/5] Starting DevGordon stack (app, Jenkins, SonarQube)..."
docker compose -f docker-ansible-compose.yml up -d app jenkins sonarqube

# Wait for services
echo "[3/5] Waiting for services to be ready (this may take 30-60 seconds)..."
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "✓ DevGordon app is ready"
        break
    fi
    echo "  Attempt $i/30 - still starting..."
    sleep 2
done

# Run Ansible tests
echo "[4/5] Running comprehensive test suite with Groq API..."
echo ""

docker compose -f docker-ansible-compose.yml run --rm ansible-runner

# Get results
echo ""
echo "[5/5] Test run complete!"
echo ""

# Display final results
LATEST_RESULT=$(ls -t test_results/test_run_groq_*.txt 2>/dev/null | head -1)
if [ -n "$LATEST_RESULT" ]; then
    echo "════════════════════════════════════════════════════════════════════════════════"
    echo "TEST RESULTS SUMMARY"
    echo "════════════════════════════════════════════════════════════════════════════════"
    tail -80 "$LATEST_RESULT"
else
    echo "⚠️  No test results found"
fi

echo ""
echo "✓ All tests completed!"
echo ""
echo "To view full results:"
echo "  cat test_results/test_run_groq_*.txt"
echo ""
echo "To keep services running for manual testing:"
echo "  docker compose -f docker-ansible-compose.yml logs -f app"
echo ""
echo "To stop all services:"
echo "  docker compose -f docker-ansible-compose.yml down"
