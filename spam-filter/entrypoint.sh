#!/bin/sh
set -e

OLLAMA_URL="${OLLAMA_URL:-http://ollama:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1:8b}"

echo "Waiting for Ollama at ${OLLAMA_URL} ..."
until wget -qO- "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; do
  sleep 3
done
echo "Ollama is up."

# Pull the model if it is not already present locally.
if ! wget -qO- "${OLLAMA_URL}/api/tags" | grep -q "\"${OLLAMA_MODEL}\""; then
  echo "Pulling model ${OLLAMA_MODEL} (this may take a while on first run) ..."
  wget -qO- "${OLLAMA_URL}/api/pull" \
    --post-data "{\"name\":\"${OLLAMA_MODEL}\"}" \
    --header "Content-Type: application/json" > /dev/null
  echo "Model ready."
else
  echo "Model ${OLLAMA_MODEL} already present."
fi

exec python main.py
