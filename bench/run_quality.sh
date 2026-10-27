#!/usr/bin/env bash
# Runs the coding-quality canary for each Strata variant on the 5090, one at a time.
# Usage: bench/run_quality.sh q2_0 iq3_xxs iq3_s
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STRATA=${STRATA:-$HOME/.local/share/strata/components/control/0.1.51/bin/strata}
PORT=18082
server=""

stop_server() {
  [ -n "$server" ] || return 0
  kill -TERM -- -"$server" 2>/dev/null
  for _ in $(seq 1 30); do kill -0 "$server" 2>/dev/null || break; sleep 1; done
  kill -KILL -- -"$server" 2>/dev/null
  for _ in $(seq 1 30); do ss -ltn | grep -q ":$PORT " || break; sleep 2; done
  server=""
}
trap 'stop_server; exit 130' INT TERM
trap stop_server EXIT

for variant in "$@"; do
  config="$ROOT/.strata/qwen-$variant.json"
  model=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['model_name'])" "$config")
  out="$ROOT/results/qwen-$variant-quality"
  if [ -e "$out/report.json" ] && grep -q '"status": "completed"' "$out/report.json"; then
    echo "[$variant] already completed, skipping"; continue
  fi
  # Never benchmark against a server we didn't start: a stray one would answer for the wrong model.
  if ss -ltn | grep -q ":$PORT "; then
    echo "[$variant] port $PORT is already in use; refusing to start (failed)"; exit 1
  fi
  rm -rf "$out"
  log="$ROOT/results/qwen-$variant-quality.server.log"
  echo "[$variant] starting server ($model)"
  setsid "$STRATA" serve --config "$config" --host 127.0.0.1 --port "$PORT" >"$log" 2>&1 &
  server=$!
  served=""
  for _ in $(seq 1 180); do
    served=$(curl -sf "http://127.0.0.1:$PORT/v1/models" | python3 -c "import json,sys;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
    [ -n "$served" ] && break
    kill -0 "$server" 2>/dev/null || break
    sleep 5
  done
  if [ "$served" = "$model" ]; then
    echo "[$variant] server ready ($served), running quality canary"
    python3 "$ROOT/bench/quality_canary.py" --base-url "http://127.0.0.1:$PORT/v1" \
      --model "$model" --label "qwen-$variant-quality" --output "$out"
  else
    echo "[$variant] server failed to start or serves '$served' instead of '$model', see $log"
  fi
  stop_server
  echo "[$variant] done"
done
