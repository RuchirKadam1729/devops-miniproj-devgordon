#!/bin/bash
# ============================================================================
# pull-ollama-model.sh — Pull Ollama model after container starts
# ============================================================================
# Run this once after docker compose up to pull the model.
# Usage: ./scripts/pull-ollama-model.sh [model_name]
#        ./scripts/pull-ollama-model.sh qwen3:8b
#        ./scripts/pull-ollama-model.sh llama3.1:8b

MODEL="${1:-qwen3:8b}"
CONTAINER="devgordon-ollama"

echo "Pulling model: $MODEL"
echo "This may take 5-15 minutes on first run..."
echo ""

docker exec $CONTAINER ollama pull "$MODEL"

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Model $MODEL ready!"
    echo "Verify with: docker exec $CONTAINER ollama list"
else
    echo "✗ Failed to pull model"
    exit 1
fi
