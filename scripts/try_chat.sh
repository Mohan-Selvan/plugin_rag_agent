#!/usr/bin/env bash
# End-to-end /chat smoke test: streams a sequence of messages through one
# session, prints the assistant text only.
set -euo pipefail
HOST="${1:-http://127.0.0.1:8001}"
SID="$(uuidgen 2>/dev/null || echo "test-$(date +%s%N)")"
echo "session=$SID"

ask() {
  local msg="$1"
  echo
  echo "=================================================="
  echo "USER: $msg"
  echo "--------------------------------------------------"
  curl -s -N -X POST "$HOST/chat" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg m "$msg" --arg s "$SID" '{message:$m, session_id:$s}')" \
  | python3 -c "
import sys, json
ev = None
for line in sys.stdin:
    line = line.rstrip('\n')
    if line.startswith('event:'):
        ev = line[6:].strip()
    elif line.startswith('data:'):
        try:
            d = json.loads(line[5:].strip())
        except Exception:
            d = {}
        if ev == 'token':
            sys.stdout.write(d.get('text', ''))
            sys.stdout.flush()
        elif ev == 'tool_start':
            sys.stdout.write(f'\n[tool: {d.get(\"name\",\"\")} ...]\n')
        elif ev == 'tool_end':
            sys.stdout.write(f'\n[tool: {d.get(\"name\",\"\")} done]\n')
        elif ev == 'error':
            sys.stdout.write(f'\n[ERROR: {d.get(\"message\",\"\")}]\n')
print()
"
}

ask "Hi there!"
ask "Tell me what you can help with."
