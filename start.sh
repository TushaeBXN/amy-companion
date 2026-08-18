#!/bin/bash
# Amy startup launcher — exports Ollama server vars before starting the server,
# then launches the Amy FastAPI server.
set -a
source "$(dirname "$0")/.env"
set +a

echo "[amy] OLLAMA_KEEP_ALIVE=$OLLAMA_KEEP_ALIVE  NUM_PARALLEL=$OLLAMA_NUM_PARALLEL  NUM_THREADS=$OLLAMA_NUM_THREADS"
echo "[amy] Starting server on :3000 ..."
# Use venv if present (required on Homebrew-managed Python systems)
if [ -f "$(dirname "$0")/venv/bin/activate" ]; then
  source "$(dirname "$0")/venv/bin/activate"
fi
exec python3 amy_server.py
