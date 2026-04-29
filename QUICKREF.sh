#!/bin/bash
# DevGordon Quick Reference Card
# Copy-paste these commands to get things done fast

echo "=== DEVGORDON QUICK REFERENCE ==="

# ============================================================================
# STARTUP
# ============================================================================

echo ""
echo "1. STARTUP (first time)"
echo "  docker compose up -d"
echo "  docker exec devgordon-ollama ollama pull llama3"
echo "  open http://localhost:8000"

# ============================================================================
# STATUS CHECKS
# ============================================================================

echo ""
echo "2. STATUS CHECKS"
echo "  # All services up?"
echo "  docker ps | grep devgordon"
echo ""
echo "  # Ollama working?"
echo "  curl http://localhost:8000/models | jq '.selected_model'"
echo ""
echo "  # Full health"
echo "  curl http://localhost:8000/health | jq '.'"

# ============================================================================
# MODELS
# ============================================================================

echo ""
echo "3. MODEL MANAGEMENT"
echo "  # List installed models"
echo "  curl http://localhost:8000/models | jq '.models'"
echo ""
echo "  # Pull a new model"
echo "  docker exec devgordon-ollama ollama pull mistral"
echo ""
echo "  # Switch models"
echo "  curl -X POST http://localhost:8000/select-model \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\": \"mistral:latest\"}'"

# ============================================================================
# USING THE UI
# ============================================================================

echo ""
echo "4. WEB UI (http://localhost:8000)"
echo "  • Type requests in natural language"
echo "  • DevGordon shows what it will do"
echo "  • Click Approve/Reject"
echo "  • See results"

# ============================================================================
# API TESTING
# ============================================================================

echo ""
echo "5. API EXAMPLES"
echo ""
echo "  # Chat"
echo "  curl -X POST http://localhost:8000/chat \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"message\": \"list pods\"}'"
echo ""
echo "  # MCP Tools Discovery"
echo "  curl http://localhost:8000/mcp/tools | jq '.tools | length'"
echo ""
echo "  # MCP Call a Tool"
echo "  curl -X POST http://localhost:8000/mcp/call \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"tool\": \"kubectl_command\", \"arguments\": {\"command\": \"get pods\"}}'"

# ============================================================================
# JENKINS SETUP
# ============================================================================

echo ""
echo "6. JENKINS SETUP (optional)"
echo "  # Get initial password"
echo "  docker exec devgordon-jenkins cat /var/jenkins_home/secrets/initialAdminPassword"
echo ""
echo "  # Open http://localhost:8080"
echo "  # Create API token, add to .env:"
echo "  echo 'JENKINS_TOKEN=your_token' > .env"
echo "  docker compose restart devgordon-app"

# ============================================================================
# DEVELOPMENT
# ============================================================================

echo ""
echo "7. DEVELOPMENT MODE (no Docker)"
echo "  cd app"
echo "  pip install -r ../requirements.txt ansible ansible-lint"
echo "  uvicorn main:app --reload --port 8000"
echo "  # In another terminal: ollama serve"

# ============================================================================
# DEBUGGING
# ============================================================================

echo ""
echo "8. DEBUGGING"
echo "  # App logs"
echo "  docker logs -f devgordon-app"
echo ""
echo "  # Ollama logs"
echo "  docker logs -f devgordon-ollama"
echo ""
echo "  # Reset everything"
echo "  docker compose down && docker compose up -d"
echo ""
echo "  # Shell into app container"
echo "  docker exec -it devgordon-app /bin/bash"

# ============================================================================
# STOPPING
# ============================================================================

echo ""
echo "9. STOPPING"
echo "  docker compose down  # Stop all services"
echo "  docker compose down -v  # Stop + remove volumes (full reset)"

echo ""
echo "=== END QUICK REFERENCE ==="
