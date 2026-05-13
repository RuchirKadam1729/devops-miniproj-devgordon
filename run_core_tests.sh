#!/bin/bash
# run_core_tests.sh
# Fast staged test of DevGordon core functionality
# Suitable for inclusion as evidence in documentation

set -e

RESULTS_DIR="./test_results"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_FILE="${RESULTS_DIR}/test_run_${TIMESTAMP}.txt"

mkdir -p "${RESULTS_DIR}"

cat > "${RESULTS_FILE}" << EOF
================================================================================
DevGordon Core Functionality Test Run
================================================================================
Timestamp: $(date)
Machine: $(hostname)
OS: $(uname -s)

STAGE 1: Environment Check
================================================================================
EOF

# STAGE 1: Environment
echo "🔍 STAGE 1: Environment Validation..." | tee -a "${RESULTS_FILE}"
{
  echo "Docker Version:"
  docker --version
  echo ""
  echo "Docker Compose Version:"
  docker compose version
  echo ""
  echo "Ollama Version:"
  ollama --version 2>&1 || echo "Ollama not running (will pull on startup)"
  echo ""
} >> "${RESULTS_FILE}" 2>&1

# STAGE 2: Start stack
echo "🚀 STAGE 2: Starting Docker Compose Stack..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Starting services: app, jenkins, sonarqube..."
  docker compose up -d --pull=always 2>&1 | grep -E "^(Creating|Starting|Running|service)" || echo "Services starting..."
} >> "${RESULTS_FILE}" 2>&1

# STAGE 3: Wait for core services
echo "⏳ STAGE 3: Waiting for Services to Stabilize..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Checking service health..."
  for i in {1..60}; do
    STATUS=$(docker compose ps app -q 2>/dev/null)
    if [ -n "$STATUS" ]; then
      echo "✓ App container running (attempt $i)"
      break
    fi
    sleep 2
  done
  echo ""
  docker compose ps 2>&1
} >> "${RESULTS_FILE}" 2>&1

# STAGE 4: Test DevGordon API endpoints
echo "🌐 STAGE 4: Testing DevGordon API Endpoints..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Testing /health endpoint..."
  sleep 5
  curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || echo "Health check failed (app may still be booting)"
  echo ""
} >> "${RESULTS_FILE}" 2>&1

# STAGE 5: Test MCP tools endpoint
echo "🔧 STAGE 5: Testing MCP Tools Endpoint..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Fetching available MCP tools..."
  TOOLS=$(curl -s http://localhost:8000/mcp/tools 2>/dev/null | python3 -c "import sys, json; data = json.load(sys.stdin); print([t['name'] for t in data.get('tools', [])])" 2>/dev/null)
  echo "Available tools: $TOOLS"
  echo ""
} >> "${RESULTS_FILE}" 2>&1

# STAGE 6: Test scan endpoint
echo "🛡️  STAGE 6: Testing Pre-Execution Security Scan..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Test 1: Safe kubectl get (should be CLEAN)..."
  curl -s -X POST http://localhost:8000/scan-code \
    -H 'Content-Type: application/json' \
    -d '{"tool_name":"kubectl_command","tool_args":{"command":"get pods"}}' \
    | python3 -m json.tool 2>/dev/null || echo "Scan failed"
  echo ""
  
  echo "Test 2: Dangerous mode 0777 (should be WARNING)..."
  curl -s -X POST http://localhost:8000/scan-code \
    -H 'Content-Type: application/json' \
    -d '{"tool_name":"run_ansible_playbook","tool_args":{"playbook_content":"---\n- hosts: localhost\n  tasks:\n    - file:\n        path: /tmp/test\n        mode: '\''0777'\''\n"}}' \
    | python3 -m json.tool 2>/dev/null || echo "Scan failed"
  echo ""
} >> "${RESULTS_FILE}" 2>&1

# STAGE 7: Test chat endpoint (simple)
echo "💬 STAGE 7: Testing Chat Endpoint (Simple Message)..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Sending: 'hello'"
  RESPONSE=$(curl -s -X POST http://localhost:8000/chat \
    -H 'Content-Type: application/json' \
    -d '{"message":"hello"}' \
    -m 30 2>/dev/null)
  
  if echo "$RESPONSE" | python3 -m json.tool > /dev/null 2>&1; then
    echo "Response received successfully:"
    echo "$RESPONSE" | python3 -m json.tool | head -30
  else
    echo "Response: $RESPONSE" | head -20
  fi
  echo ""
} >> "${RESULTS_FILE}" 2>&1

# STAGE 8: Run pytest tests
echo "✅ STAGE 8: Running Integration Tests..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Running pytest (non-slow tests only)..."
  cd . && pytest tests/test_devgordon.py -v -m 'not slow' --tb=short 2>&1 | tail -50
} >> "${RESULTS_FILE}" 2>&1

# STAGE 9: System stats
echo "📊 STAGE 9: System Resource Stats..." | tee -a "${RESULTS_FILE}"
{
  echo ""
  echo "Docker Disk Usage:"
  docker system df 2>&1
  echo ""
  echo "Container Stats:"
  docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>&1 || echo "Stats unavailable"
  echo ""
  echo "Disk Usage:"
  df -h / | tail -1
  echo ""
} >> "${RESULTS_FILE}" 2>&1

# Final summary
{
  echo ""
  echo "================================================================================"
  echo "Test Run Completed: $(date)"
  echo "Results saved to: ${RESULTS_FILE}"
  echo "================================================================================"
} >> "${RESULTS_FILE}"

echo ""
echo "✓ Test run complete!"
echo "Results saved to: ${RESULTS_FILE}"
echo ""
echo "View results:"
echo "  cat ${RESULTS_FILE}"
echo ""
echo "Display key sections:"
echo "  grep -A 20 'STAGE 1' ${RESULTS_FILE}"
echo ""

# Display first part of results
cat "${RESULTS_FILE}"
