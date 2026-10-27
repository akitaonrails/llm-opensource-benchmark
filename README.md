# llm-opensource-benchmark

Speed and coding-quality benchmarks for quantized versions of **Qwen3.8 Flash-Next** served by the Strata engine, on two local machines:

- **RTX 5090** (32 GB VRAM, desktop). Every run keeps 4 GB of VRAM free (`--vram-reserve-mib 4096`) so Hyprland and apps keep working. Strata control 0.1.51, engine 0.1.50 (CUDA).
- **Strix Halo** at 192.168.0.90 (headless; 64 GB VRAM carve-out + 31 GB GTT, 61 GB RAM). Uses the whole GPU. Strata 0.1.45.3 `hip-halo`; no newer engine build exists for gfx1151.

The four versions tested were Q2_0, IQ3_XXS, IQ3_S and Unsloth UD-Q4_K_XL. Qwen 4 and Llama were skipped by choice. DeepSeek V4.1 got a calibration run on the 5090 and was dropped at about 17 tok/s.

## Results

### Speed

Output speed, prompt-reading speed at 32k tokens, and time to first token on a cold 32k prompt.

| Version | 5090 output tok/s | 5090 reading tok/s | 5090 first token | Strix output tok/s | Strix reading tok/s | Strix first token |
|---|---|---|---|---|---|---|
| Q2_0 | 245–270 | 6,200 | 7 s | 55–65 | 371 | 114 s |
| IQ3_XXS | 200–240 | 5,100 | 8 s | 50–57 | 347 | 122 s |
| IQ3_S | 160–210 | 6,200 | 7 s | 47–51 | 338 | 126 s |
| UD-Q4_K_XL | 44–52 | 2,300 | 19 s | 17 (one 4k request) | 137 | — |

Q4_K_XL doesn't fit either machine with this setup:
- **5090:** it runs slowly, and the quality run was killed by `systemd-oomd`.
- **Strix:** VRAM reached zero, the GPU started paging, and the machine hung until it was rebooted.

### Coding quality

| Test | Q2_0 | IQ3_XXS | IQ3_S |
|---|---|---|---|
| Quality canary, 5090 (1 run) | 100 | 100 | 100 |
| Quality canary, Strix (1 run) | 100 | 100 | 100 |
| Hard canary, 5090 (3 runs) | 100 / 100 / 100 | — | 100 / 100 / 100 |
| Hard canary average time for 4 turns | 7.1 min | — | 12.0 min |

Every version passed every hidden test in both coding tests, so neither test found a quality difference between them.
- **Speed:** on the hard test Q2_0 reached the same result in about 60% of IQ3_S's time.
- **Effort:** both needed a similar number of failing test runs before getting everything right: 2/10/6 for Q2_0 and 2/10/4 for IQ3_S.
- **Recommendation:** Q2_0 is the better pick for opencode on the 5090.
- **Untested:** very long contexts (over 100k tokens) and larger codebases, where 2-bit versions usually lose quality first.

## The tests

### Speed canary (`bench/canary.py`)
Starts the server itself and sends three workloads, each generating 256 tokens:
- a cold 4k-token prompt;
- a cold 32k-token prompt;
- a 32k-token follow-up that reuses the conversation cache.

It records time to first token, prompt and output tok/s, and GPU and system samples. GPU readings come from `nvidia-smi`, or from AMD sysfs on the Strix. The report is written to `report.json`.

### Quality canary (`bench/quality_canary.py`, fixture `bench/fixture/logstats`)
opencode 2.1.4 works on a streaming log analyzer over three turns in one session:
1. implement the spec, given a 48k-token transcript;
2. fix the reported bugs;
3. handle changed requirements, add regression tests and audit the result.

Graded with 9 hidden tests (70 points), requirement checks (20) and process checks (10).

### Hard canary (`bench/hard_canary.py`, fixture `bench/fixture/ledger`)
A payment-event reconciler worked on over four turns:
1. implement the spec;
2. handle an exporter that writes events out of order;
3. add chargebacks and a report currency;
4. refactor the conversion code into its own module, then audit everything against all earlier requests.

What makes it harder:
- **Buried decisions:** the first prompt is about 64k tokens (83k with opencode's system prompt). Binding decisions sit at fixed depths. One decision is corrected later in the transcript, and some proposals are explicitly rejected.
- **Exact money:** amounts must be exact decimals, rounded once with round-half-even. Code using floats or rounding per event gets the cents wrong.
- **Interacting rules:** later turns change rules that interact with earlier ones.

Grading:
- 36 hidden tests in `bench/hard_oracle/`, grouped as `spec`, `context`, `turn2`, `turn3` and `turn4` and reported per group.
- Requirement checks:
  - the supplied tests are unchanged;
  - there are no dependencies;
  - there was no attempt to reach the hidden tests;
  - regression tests were added;
  - no `float()` is used for money.
- Check of the test itself: the starting fixture passes 6 of the 36 hidden tests, and the reference solution in `bench/hard_reference/` passes all 36.

### Isolation (`bench/sandbox.py`)
Every opencode turn, and the grading, runs inside bubblewrap with `--unshare-all`, so it has no network.
- **What it can see:** `/usr`, the project at `/work`, a private home and the read-only files bound in.
- **Only way out:** a Unix-socket gateway that forwards `GET /v1/models` and `POST /v1/chat/completions` for the expected model and nothing else. Inside the sandbox, socat bridges it to localhost.
- **Hidden tests:** bound in only for grading, after the model's last turn.
- **Afterwards:** the run logs are scanned for any attempt to reach the hidden tests.

## Running

```sh
# 5090 (servers on port 18082; refuses to start if the port is busy)
bench/run_quality.sh q2_0 iq3_xxs iq3_s
REPS=3 bench/run_hard.sh q2_0 iq3_s

# Strix: speed runs on the Strix; quality runs here against the Strix server
bench/run_strix.sh q2_0 iq3_xxs iq3_s
```

- **Server configs:** in `configs/5090` and `configs/strix`. The 5090 ones point at `.strata/`, which holds the models and packs, is about 277 GB and is not committed.
- **Packs:**
  - Q2_0: built with `strata_pack.py build`, `pack_index.py` and `strata_tokenizer.py`.
  - IQ3 versions: built with `iq_pack.py`.
  - Unsloth Q4_K_XL: built with `iq_pack.py --compat-bf16`.
  - The Strix commands are in `bench/strix_build_pack.sh`.
- **Strix network setup:**
  - Its sshd sets `AllowTcpForwarding no`, so `bench/strix_relay.sh` carries each connection over an SSH command instead of a tunnel.
  - The Strix normally runs the non-coding benchmark's IQ3_S server on port 18081. `results/strix/restore-18081.md` shows how to restore it.

## Results layout

Each run folder has a `report.json` with the scores, timings and per-test results, plus logs.
- **Left out of git:** the model's working copy (`project/`), private homes, raw opencode streams and long prompts.
- **Where things are:**
  - `results/qwen-<version>-speed-elastic` and `results/qwen-<version>-quality`: 5090 runs;
  - `results/strix/`: Strix runs;
  - `results/hard/`: hard canary runs.
