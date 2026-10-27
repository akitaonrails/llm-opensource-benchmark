#!/usr/bin/env bash
# Speed then quality canary for each Strata variant on the Strix Halo, one variant at a time.
# Speed runs on the Strix (canary.py starts and stops the server there); quality runs here,
# sandboxed, against the Strix server through an SSH tunnel.
# Usage: bench/run_strix.sh q2_0 iq3_xxs iq3_s q4_k_xl
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST=${STRIX_HOST:-192.168.0.90}
O=/var/strata-models/osb
PY=${STRIX_PY:-/home/akitaonrails/.local/share/strata/terminal/0.1.45.3/python/bin/python3.12}
STRATA=${STRIX_STRATA:-/home/akitaonrails/.local/share/strata/terminal/0.1.45.3/strata.py}
PORT=18083
OUT="$ROOT/results/strix"
mkdir -p "$OUT"
tunnel=""

remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$@"; }

stop_remote_server() {
  remote "if [ -f $O/server.pgid ]; then kill -TERM -- -\$(cat $O/server.pgid) 2>/dev/null; \
    for i in \$(seq 1 60); do ss -ltn | grep -q ':$PORT ' || break; sleep 2; done; \
    kill -KILL -- -\$(cat $O/server.pgid) 2>/dev/null; rm -f $O/server.pgid; fi"
}
stop_tunnel() { [ -n "$tunnel" ] && kill "$tunnel" 2>/dev/null; tunnel=""; }
cleanup() { stop_tunnel; stop_remote_server; }
trap 'cleanup; exit 130' INT TERM
trap cleanup EXIT

port_busy() { remote "ss -ltn | grep -q ':$PORT '"; }

for variant in "$@"; do
  config="$O/configs/qwen-$variant.json"
  model=$(remote "python3 -c \"import json;print(json.load(open('$config'))['model_name'])\"")
  pack=$(remote "python3 -c \"import json;a=json.load(open('$config'))['args'];print(a[a.index('--pack')+1])\"")
  if ! remote "test -f $pack/tokenizer/vocab.json"; then
    echo "[$variant] pack not ready at $pack (failed)"; continue
  fi
  if port_busy || ss -ltn | grep -q ":$PORT "; then
    echo "[$variant] port $PORT already in use; refusing to start (failed)"; exit 1
  fi

  # Speed: on the Strix, full GPU (headless, no reserve).
  speed="$OUT/qwen-$variant-speed"
  if [ -e "$speed/report.json" ] && grep -q '"status": "completed"' "$speed/report.json"; then
    echo "[$variant] speed already completed, skipping"
  else
    rm -rf "$speed"; remote "rm -rf $O/results/qwen-$variant-speed"
    echo "[$variant] speed canary starting ($model)"
    remote "cd $O && python3 canary.py --base-url http://127.0.0.1:$PORT/v1 --model $model \
      --label strix-qwen-$variant --output $O/results/qwen-$variant-speed --reserve-mib 0 \
      -- $PY $STRATA serve --config $config --host 127.0.0.1 --port $PORT --offline --shutdown-timeout 1" \
      > "$OUT/qwen-$variant-speed.stdout" 2>&1
    rsync -a "$HOST:$O/results/qwen-$variant-speed/" "$speed/"
    remote "cp $O/logs/qwen-$variant.engine.log $O/results/qwen-$variant-speed/engine.log 2>/dev/null" || true
    rsync -a "$HOST:$O/results/qwen-$variant-speed/engine.log" "$speed/" 2>/dev/null || true
    echo "[$variant] speed done: $(python3 -c "import json;print(json.load(open('$speed/report.json'))['status'])" 2>/dev/null || echo missing)"
  fi

  # Quality: server on the Strix, sandboxed opencode here through a tunnel.
  quality="$OUT/qwen-$variant-quality"
  if [ -e "$quality/report.json" ] && grep -q '"status": "completed"' "$quality/report.json"; then
    echo "[$variant] quality already completed, skipping"; continue
  fi
  rm -rf "$quality"
  for _ in $(seq 1 30); do port_busy || break; sleep 2; done
  echo "[$variant] quality: starting server ($model)"
  # cd on its own line: "cd && cmd &" would background a subshell that keeps ssh's pipes open and ssh never returns.
  # setsid makes the server its own process group, so $! is also the group to stop later.
  remote "cd $O; setsid $PY $STRATA serve --config $config --host 127.0.0.1 --port $PORT --offline \
    --shutdown-timeout 1 > $O/logs/qwen-$variant-quality.server.log 2>&1 < /dev/null & echo \$! > $O/server.pgid"
  # The Strix forbids SSH port forwarding; each connection goes through an SSH command instead.
  socat TCP-LISTEN:$PORT,bind=127.0.0.1,reuseaddr,fork EXEC:"$ROOT/bench/strix_relay.sh" &
  tunnel=$!
  served=""
  for _ in $(seq 1 240); do
    served=$(curl -sf "http://127.0.0.1:$PORT/v1/models" | python3 -c "import json,sys;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
    [ -n "$served" ] && break
    remote "test -f $O/server.pgid && kill -0 \$(cat $O/server.pgid) 2>/dev/null" || break
    sleep 5
  done
  if [ "$served" = "$model" ]; then
    echo "[$variant] server ready ($served), running quality canary"
    python3 "$ROOT/bench/quality_canary.py" --base-url "http://127.0.0.1:$PORT/v1" --model "$model" \
      --provider strata-strix --label "strix-qwen-$variant-quality" --output "$quality" --turn-timeout 1800
  else
    echo "[$variant] server failed to start or serves '$served' instead of '$model' (failed)"
  fi
  stop_tunnel
  stop_remote_server
  rsync -a "$HOST:$O/logs/qwen-$variant-quality.server.log" "$OUT/" 2>/dev/null || true
  echo "[$variant] done"
done
echo "STRIX_ALL_DONE"
