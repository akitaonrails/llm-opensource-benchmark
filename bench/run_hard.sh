#!/usr/bin/env bash
# Hard coding canary on the 5090: one server per variant, REPS sandboxed runs each.
# Usage: REPS=3 bench/run_hard.sh q2_0 iq3_s
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STRATA=${STRATA:-$HOME/.local/share/strata/components/control/0.1.51/bin/strata}
PORT=18082
REPS=${REPS:-3}
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
  pending=()
  for rep in $(seq 1 "$REPS"); do
    out="$ROOT/results/hard/qwen-$variant-r$rep"
    if [ -e "$out/report.json" ] && grep -q '"status": "completed"' "$out/report.json"; then
      echo "[$variant r$rep] already completed, skipping"
    else
      pending+=("$rep")
    fi
  done
  [ ${#pending[@]} -gt 0 ] || continue
  # Never benchmark against a server we didn't start: a stray one would answer for the wrong model.
  if ss -ltn | grep -q ":$PORT "; then
    echo "[$variant] port $PORT is already in use; refusing to start (failed)"; exit 1
  fi
  mkdir -p "$ROOT/results/hard"
  log="$ROOT/results/hard/qwen-$variant.server.log"
  echo "[$variant] starting server ($model)"
  setsid "$STRATA" serve --config "$config" --host 127.0.0.1 --port "$PORT" >>"$log" 2>&1 &
  server=$!
  served=""
  for _ in $(seq 1 180); do
    served=$(curl -sf "http://127.0.0.1:$PORT/v1/models" | python3 -c "import json,sys;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
    [ -n "$served" ] && break
    kill -0 "$server" 2>/dev/null || break
    sleep 5
  done
  if [ "$served" != "$model" ]; then
    echo "[$variant] server failed to start or serves '$served' instead of '$model', see $log"
    stop_server; continue
  fi
  for rep in "${pending[@]}"; do
    out="$ROOT/results/hard/qwen-$variant-r$rep"
    rm -rf "$out"
    echo "[$variant r$rep] running hard canary"
    python3 "$ROOT/bench/hard_canary.py" --base-url "http://127.0.0.1:$PORT/v1" \
      --model "$model" --label "hard-qwen-$variant-r$rep" --output "$out"
    kill -0 "$server" 2>/dev/null || { echo "[$variant] server died"; server=""; break; }
  done
  stop_server
  echo "[$variant] done"
done
echo "HARD_ALL_DONE"
