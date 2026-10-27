#!/usr/bin/env bash
# Basalt on the 5090: speed canary, then REPS sandboxed hard-canary runs, per version.
# Same harness, port, VRAM reserve and context as the Strata runs, so the numbers compare.
# Usage: REPS=3 bench/run_basalt.sh iq3_s q2_0
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASALT_DIR=${BASALT_DIR:-$ROOT/.basalt/src}
BASALT_PY=${BASALT_PY:-$ROOT/.basalt/venv/bin/python}
PORT=18082
REPS=${REPS:-3}
OUT="$ROOT/results/basalt"
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

completed() { [ -e "$1/report.json" ] && grep -q '"status": "completed"' "$1/report.json"; }

mkdir -p "$OUT"
for variant in "$@"; do
  config="$ROOT/configs/5090/basalt-$variant.json"
  model=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['server']['alias'])" "$config")
  serve=("$BASALT_DIR/bin/basalt" serve --config "$config" --python "$BASALT_PY")
  if ss -ltn | grep -q ":$PORT "; then
    echo "[$variant] port $PORT is already in use; refusing to start (failed)"; exit 1
  fi

  speed="$OUT/$variant-speed"
  if completed "$speed"; then
    echo "[$variant] speed already completed, skipping"
  else
    rm -rf "$speed"
    echo "[$variant] speed canary starting ($model)"
    python3 "$ROOT/bench/canary.py" --base-url "http://127.0.0.1:$PORT/v1" --model "$model" \
      --label "basalt-$variant" --output "$speed" --reserve-mib 4096 -- "${serve[@]}"
    echo "[$variant] speed done"
  fi

  pending=()
  for rep in $(seq 1 "$REPS"); do completed "$OUT/$variant-hard-r$rep" || pending+=("$rep"); done
  [ ${#pending[@]} -gt 0 ] || continue
  for _ in $(seq 1 30); do ss -ltn | grep -q ":$PORT " || break; sleep 2; done
  log="$OUT/$variant.server.log"
  echo "[$variant] starting server for the hard canary"
  setsid "${serve[@]}" >>"$log" 2>&1 < /dev/null &
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
    out="$OUT/$variant-hard-r$rep"
    rm -rf "$out"
    echo "[$variant r$rep] running hard canary"
    python3 "$ROOT/bench/hard_canary.py" --base-url "http://127.0.0.1:$PORT/v1" --provider basalt-5090 \
      --model "$model" --label "basalt-$variant-hard-r$rep" --output "$out"
    kill -0 "$server" 2>/dev/null || { echo "[$variant] server died"; server=""; break; }
  done
  stop_server
  echo "[$variant] done"
done
echo "BASALT_ALL_DONE"
