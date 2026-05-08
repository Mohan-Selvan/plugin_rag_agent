#!/usr/bin/env bash
# Optional helper: pull the ollama:* models named in config/config.yaml
# onto a locally-running Ollama server.
#
# Plugin RAG itself does not manage Ollama - the operator runs whatever
# local LLM server they prefer (Ollama, llama.cpp, vLLM, LM Studio, ...).
# This script just removes the manual model-pull step when the choice is
# Ollama on the same host as Plugin RAG. Skip it if you use a cloud LLM,
# a different local LLM server, or if the models are already pulled.
#
# Usage:
#   scripts/setup_ollama.sh                 # uses config/config.yaml
#   scripts/setup_ollama.sh path/to/cfg     # custom config path
#
# Requirements:
#   - The 'ollama' CLI installed and the daemon running (ollama serve).
#     Install instructions: https://ollama.com
#   - Python 3 + PyYAML (already in requirements.txt; or system python).
set -euo pipefail

CONFIG_FILE="${1:-${PLUGIN_RAG_CONFIG:-./config/config.yaml}}"

log()  { echo "[setup-ollama] $*"; }
fail() { echo "[setup-ollama] ERROR: $*" >&2; exit 1; }

[[ -f "$CONFIG_FILE" ]] || fail "config file not found at ${CONFIG_FILE}"
command -v ollama >/dev/null \
  || fail "the 'ollama' CLI is not installed - see https://ollama.com"

# Verify the daemon is reachable so failures here are clearly diagnosable.
if ! curl -fsS --max-time 5 "${OLLAMA_HOST:-http://localhost:11434}/api/tags" \
     > /dev/null; then
  fail "Ollama daemon not reachable at ${OLLAMA_HOST:-http://localhost:11434}; start it with 'ollama serve'"
fi

models=$(python3 - "$CONFIG_FILE" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
seen = set()
for key in ("agent", "ingestion", "embeddings"):
    m = (cfg.get(key) or {}).get("model", "")
    if isinstance(m, str) and m.startswith("ollama:") and m not in seen:
        seen.add(m); print(m.split(":", 1)[1])
PY
)

if [[ -z "$models" ]]; then
  log "no ollama:* models found in ${CONFIG_FILE}; nothing to pull"
  exit 0
fi

while read -r model; do
  [[ -z "$model" ]] && continue
  log "pulling ${model}"
  ollama pull "$model" || fail "failed to pull ${model}"
done <<< "$models"

log "done. configured ollama models are present:"
ollama list
