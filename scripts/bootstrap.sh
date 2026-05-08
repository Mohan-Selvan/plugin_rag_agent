#!/usr/bin/env bash
# Container entrypoint.
#
# Fail-fast policy: any unmet dependency, missing credential, missing
# knowledge base, or failed ingestion exits the container with a non-zero
# status. Operators must fix the cause and restart - silent degradation
# is never acceptable.
#
# - Cloud providers in config must have their API key set.
# - ollama:* models in config must be reachable on OLLAMA_HOST AND already
#   present (operators run their own LLM server; see scripts/setup_ollama.sh
#   for an optional helper).
# - Ingestion runs once per volume (gated by storage/.ingested) and the
#   ``data/`` folder must contain at least one markdown file.
# - On every successful path the script ends by exec'ing uvicorn.
set -euo pipefail

CMD="${1:-serve}"
CONFIG_FILE="${PLUGIN_RAG_CONFIG:-/app/config/config.yaml}"
MARKER_DIR="/app/storage"
INGEST_MARKER="${MARKER_DIR}/.ingested"
DATA_DIR="${DATA_DIR:-/app/data}"
# Resolve OLLAMA_HOST/QDRANT_URL once and re-export so any Python child
# (ingestion, uvicorn) sees exactly what the bootstrap probed - prevents
# the "curl works but Python falls back to localhost" desync when the
# operator leaves the .env line commented out.
export OLLAMA_HOST="${OLLAMA_HOST:-http://host.docker.internal:11434}"
export QDRANT_URL="${QDRANT_URL:-http://qdrant:6333}"
OLLAMA_URL="${OLLAMA_HOST}"
QDRANT_BASE="${QDRANT_URL}"

mkdir -p "${MARKER_DIR}"

log()  { echo "[bootstrap] $*"; }
fail() { echo "[bootstrap] ERROR: $*" >&2; exit 1; }

# Print ollama:* model names referenced in config.yaml, one per line.
extract_ollama_models() {
  python - "$CONFIG_FILE" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
seen = set()
for key in ("agent", "ingestion", "embeddings"):
    m = (cfg.get(key) or {}).get("model", "")
    if isinstance(m, str) and m.startswith("ollama:") and m not in seen:
        seen.add(m); print(m.split(":", 1)[1])
PY
}

# Print provider names (other than ollama) referenced in config.yaml.
extract_cloud_providers() {
  python - "$CONFIG_FILE" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
seen = set()
for key in ("agent", "ingestion", "embeddings"):
    m = (cfg.get(key) or {}).get("model", "")
    if isinstance(m, str) and ":" in m:
        prov = m.split(":", 1)[0]
        if prov and prov != "ollama":
            seen.add(prov)
for p in sorted(seen): print(p)
PY
}

# Block until $1 returns 200 within $3 seconds; fail otherwise.
wait_for_url() {
  local url="$1" name="$2" timeout="${3:-60}"
  local waited=0
  until curl -fsS -o /dev/null --max-time 3 "$url"; do
    if (( waited >= timeout )); then
      fail "${name} not reachable at ${url} after ${timeout}s"
    fi
    sleep 2; waited=$((waited + 2))
  done
  log "${name} reachable"
}

# Verify each ollama:* model in config is present on the operator's
# Ollama server. The container never pulls; that's the operator's job
# (run scripts/setup_ollama.sh or pull manually).
verify_ollama_models() {
  local tags
  tags=$(curl -fsS --max-time 10 "${OLLAMA_URL}/api/tags") \
    || fail "failed to list models from Ollama at ${OLLAMA_URL}"
  while read -r model; do
    [[ -z "$model" ]] && continue
    if ! echo "$tags" | grep -qE "\"name\"[[:space:]]*:[[:space:]]*\"${model}\""; then
      fail "ollama model '${model}' is not present on ${OLLAMA_URL}; pull it on your LLM host (e.g. 'ollama pull ${model}') or run scripts/setup_ollama.sh"
    fi
    log "verified ollama model is present: ${model}"
  done
}

# For each cloud provider listed in config, verify its API key is set.
verify_cloud_keys() {
  while read -r prov; do
    [[ -z "$prov" ]] && continue
    case "$prov" in
      openai)
        [[ -n "${OPENAI_API_KEY:-}"    ]] || fail "OPENAI_API_KEY is required (config uses openai:* model)" ;;
      anthropic)
        [[ -n "${ANTHROPIC_API_KEY:-}" ]] || fail "ANTHROPIC_API_KEY is required (config uses anthropic:* model)" ;;
      google_genai|gemini|google)
        [[ -n "${GOOGLE_API_KEY:-}"    ]] || fail "GOOGLE_API_KEY is required (config uses ${prov}:* model)" ;;
      *)
        log "WARNING: unknown provider '${prov}' - skipping credential check" ;;
    esac
  done
}

case "${CMD}" in
  serve)
    [[ -f "${CONFIG_FILE}" ]] || fail "config file not found at ${CONFIG_FILE}"

    # Pre-flight 1: cloud providers in config must have API keys.
    extract_cloud_providers | verify_cloud_keys

    # Pre-flight 2: any ollama:* models must be reachable + already pulled
    # on the operator's Ollama server. We don't manage Ollama; the operator
    # does (see scripts/setup_ollama.sh).
    ollama_models=$(extract_ollama_models)
    if [[ -n "$ollama_models" ]]; then
      wait_for_url "${OLLAMA_URL}/api/tags" "ollama"
      echo "$ollama_models" | verify_ollama_models
    fi

    # Optional resets driven by env vars passed from `docker compose up`.
    # RESET=1 wipes chat history AND forces re-ingestion.
    # REINGEST=1 forces re-ingestion only (keeps chat history).
    if [[ "${RESET:-0}" == "1" ]]; then
      log "RESET=1: clearing sessions.sqlite + ingestion marker"
      rm -f "${MARKER_DIR}/.ingested" "${MARKER_DIR}/sessions.sqlite" "${MARKER_DIR}/sessions.sqlite-journal"
    elif [[ "${REINGEST:-0}" == "1" ]]; then
      log "REINGEST=1: clearing ingestion marker (chat history preserved)"
      rm -f "${MARKER_DIR}/.ingested"
    fi

    # Pre-flight 3: ingestion (one-shot per volume, gated by marker).
    if [[ "${SKIP_INGEST:-0}" != "1" && ! -f "${INGEST_MARKER}" ]]; then
      [[ -d "${DATA_DIR}" ]] || fail "data folder ${DATA_DIR} does not exist"
      shopt -s nullglob globstar
      md_files=("${DATA_DIR}"/**/*.md)
      shopt -u nullglob globstar
      (( ${#md_files[@]} > 0 )) \
        || fail "no markdown files found under ${DATA_DIR}; drop knowledge-base content there before starting"

      wait_for_url "${QDRANT_BASE}/readyz" "qdrant"
      log "running ingestion (${#md_files[@]} markdown files)"
      python -m backend.rag.ingest --recreate \
        || fail "ingestion failed; check logs above"
      touch "${INGEST_MARKER}"
    else
      log "skipping ingestion (marker present or SKIP_INGEST=1)"
    fi

    log "starting uvicorn on ${HOST:-0.0.0.0}:${PORT:-8000}"
    exec uvicorn backend.server.server:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
    ;;
  ingest)
    shift || true
    exec python -m backend.rag.ingest "$@"
    ;;
  shell)
    exec /bin/bash
    ;;
  *)
    exec "$@"
    ;;
esac
