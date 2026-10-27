# Setup and running

## Requirements

- **OS:** Linux, with [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`), `socat`, `git`, `curl`, `rsync` and Python 3.11 or newer.
- **Strata:** installed. These runs used control 0.1.51 on the 5090, and 0.1.45.3 `hip-halo` on the Strix Halo.
- **opencode:** version 2.1.4. The sandbox binds the native binary, not the Node launcher. Its default path assumes a [mise](https://mise.jdx.dev) install; point `OPENCODE_BINARY` at your own copy if it lives elsewhere.
- **Model files:** the GGUF files for each version, plus the Strata MTP draft (`--mtp`).

## Environment overrides

| Variable | Used by | Default |
|---|---|---|
| `STRATA` | `run_quality.sh`, `run_hard.sh` | `~/.local/share/strata/components/control/0.1.51/bin/strata` |
| `OPENCODE_BINARY` | all coding canaries | the mise opencode 2.1.4 native binary |
| `REPS` | `run_hard.sh` | `3` |
| `STRIX_HOST` | `run_strix.sh`, `strix_relay.sh` | `192.168.0.90` |
| `STRIX_PY`, `STRIX_STRATA` | `run_strix.sh` | the Strata 0.1.45.3 terminal install on the Strix |

## Server configs

Strata server configs are in [`configs/5090`](../configs/5090) and [`configs/strix`](../configs/strix). The drivers read the 5090 ones from `.strata/qwen-<version>.json`, so copy them there and edit the model and pack paths. `.strata/` holds the models and packs (about 277 GB), so it is not committed.

The key flags:
- **5090:**
  - `--vram-elastic --vram-reserve-mib 4096`, which leaves room for the desktop;
  - `--kv int8 --max-context 131072`;
  - `--spec 4` with the MTP draft.
- **Strix:**
  - `--resident-experts` (or `--resident-budget-gib 38` for Q4_K_XL);
  - `--kv int8 --max-context 131072`;
  - `STRATA_RESIDENT_HEADROOM_GIB=8`.

## Building packs

Strata serves each version from a pack built from its GGUF:

| Version | Tools |
|---|---|
| Q2_0 | `strata_pack.py build`, then `pack_index.py` and `strata_tokenizer.py` |
| IQ3_XXS, IQ3_S | `iq_pack.py` |
| UD-Q4_K_XL (Unsloth) | `iq_pack.py --compat-bf16` |

[`bench/strix_build_pack.sh`](../bench/strix_build_pack.sh) has the exact commands used on the Strix. It deliberately builds into its own folder. Strata's `setup.py --data-dir` moves model files from other installs on the same drive, so it was avoided on a shared machine.

## Running

### 5090

```sh
bench/run_quality.sh q2_0 iq3_xxs iq3_s      # quality canary, one run per version
REPS=3 bench/run_hard.sh q2_0 iq3_s          # hard canary, three runs per version
```

Both drivers:
- start one server per version on port 18082, and refuse to start if that port is already in use;
- check that the server reports the expected model name before any test runs;
- stop the server's whole process group when they finish or are interrupted;
- skip runs that already have a completed report, so a stopped batch can be resumed.

To run a single canary against a server that is already up:

```sh
python3 bench/hard_canary.py --base-url http://127.0.0.1:18082/v1 \
  --model qwen3.8-flash-next-q2_0 --label my-run --output results/hard/my-run
```

To see the long first prompt without running anything, add `--print-prompt`.

### Strix Halo

```sh
bench/run_strix.sh q2_0 iq3_xxs iq3_s
```

For each version it:
1. runs the speed canary on the Strix itself, then copies the report back;
2. starts the server on the Strix, port 18083;
3. runs the quality canary here, sandboxed as usual, against that server.

That Strix's sshd sets `AllowTcpForwarding no`, so `ssh -L` won't work. Instead:
- a local `socat` listener runs [`bench/strix_relay.sh`](../bench/strix_relay.sh) for each connection;
- the relay carries the bytes over an SSH command, reusing one shared SSH connection.

The Strix normally hosts the non-coding benchmark's IQ3_S server on port 18081. It was stopped for these runs and restored afterwards; see [`results/strix/restore-18081.md`](../results/strix/restore-18081.md).

## Gotchas found along the way

- **Long prompts:** Linux limits a single command-line argument to 128 KiB. Prompts longer than 64 KiB are passed to opencode as an attached read-only file (`--file`).
- **Out-of-memory killer:** `systemd-oomd` kills the whole tmux scope under memory pressure. Run long batches detached with `setsid nohup`.
- **Remote launches:** `cd dir && setsid server &` over ssh keeps ssh's pipes open, so ssh never returns. Use `cd dir; setsid server > log 2>&1 < /dev/null &`.
- **Killing processes:** `pkill -f pattern` can match the shell that runs it. Kill by PID or process group instead.
