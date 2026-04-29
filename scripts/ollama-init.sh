#!/bin/bash
# ============================================================================
# ollama-init.sh — Auto-pull Ollama model on first run
# ============================================================================
# This script runs inside the Ollama container and automatically downloads
# the model (qwen3:8b by default) when the container starts.

MODEL_NAME="${OLLAMA_MODEL:-qwen3:8b}"
OLLAMA_HOST="http://localhost:11434"

echo "[ollama-init] Starting Ollama server..."

# Start Ollama server in background
/usr/bin/ollama serve > /tmp/ollama.log 2>&1 &
OLLAMA_PID=$!

# Wait for server to be ready (up to 60s)
echo "[ollama-init] Waiting for Ollama API to respond..."
READY=0
for i in {1..60}; do
    if curl -s "$OLLAMA_HOST/api/tags" > /dev/null 2>&1; then
        echo "[ollama-init] Ollama is ready!"
        READY=1
        break
    fi
    echo "[ollama-init] Waiting... ($i/60)"
    sleep 1
done

if [ $READY -eq 0 ]; then
    echo "[ollama-init] ERROR: Ollama failed to start after 60 seconds"
    echo "[ollama-init] Server logs:"
    cat /tmp/ollama.log
    kill $OLLAMA_PID 2>/dev/null || true
    exit 1
fi

# Check if model is already loaded
echo "[ollama-init] Checking for model: $MODEL_NAME"
MODELS=$(curl -s "$OLLAMA_HOST/api/tags" | grep -o '"name":"[^"]*"' | grep "$MODEL_NAME" || true)

if [ -n "$MODELS" ]; then
    echo "[ollama-init] Model $MODEL_NAME already loaded"
else
    echo "[ollama-init] Pulling model: $MODEL_NAME (this may take several minutes)..."
    /usr/bin/ollama pull "$MODEL_NAME" 2>&1 | tee -a /tmp/ollama.log
    if [ ${PIPESTATUS[0]} -eq 0 ]; then
        echo "[ollama-init] Model $MODEL_NAME pulled successfully"
    else
        echo "[ollama-init] ERROR: Failed to pull model $MODEL_NAME"
        kill $OLLAMA_PID 2>/dev/null || true
        exit 1
    fi
fi

echo "[ollama-init] Setup complete. Ollama ready with model: $MODEL_NAME"

# Keep container alive by waiting for server process
wait $OLLAMA_PID
