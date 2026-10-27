# llm-opensource-benchmark

**Which quantized version of Qwen3.8 Flash-Next should I run locally for coding agents?**

This repo answers that for two home machines, using [opencode](https://opencode.ai) as the coding agent. The models are served by the Strata inference engine and, on the 5090, also by [Basalt](https://github.com/jesdga95/basalt), a Strata fork tuned for RTX 50-series GPUs:

| Machine | GPU memory | Notes |
|---|---|---|
| RTX 5090 desktop | 32 GB VRAM | 4 GB always kept free for the desktop |
| AMD Strix Halo (headless) | 64 GB VRAM + 31 GB GTT | whole GPU available |

Versions tested: **Q2_0, IQ3_XXS, IQ3_S** and **Unsloth UD-Q4_K_XL**.

## Goals

1. **Speed.** Measure how fast each version writes output, how fast it reads long prompts, and how long you wait for the first token. These are the numbers that decide whether an agent feels usable.
2. **Coding quality.** Find out whether lower-bit versions write worse code in a realistic multi-turn agent session with long context and changing requirements.
3. **Fair grading.** Grade with hidden tests in a sandbox the model can't escape or peek out of, so a high score means working code.

## Results at a glance

### Strata

| Version | 5090 output | 5090 first token (32k prompt) | Strix output | Hard coding test (5090) |
|---|---|---|---|---|
| Q2_0 | 245–270 tok/s | 7 s | 55–65 tok/s | 100 / 100 / 100, avg 7.1 min |
| IQ3_XXS | 200–240 tok/s | 8 s | 50–57 tok/s | — |
| IQ3_S | 160–210 tok/s | 7 s | 47–51 tok/s | 100 / 100 / 100, avg 12.0 min |
| UD-Q4_K_XL | 44–52 tok/s | 19 s | did not fit | did not fit |

### Basalt (5090 only)

| Version | Output | First token (32k prompt) | Hard coding test |
|---|---|---|---|
| **Q2_0** | **377–400 tok/s** | **4.9 s** | **100 / 100 / 100, avg 5.5 min** |
| IQ3_S | 223–231 tok/s | 5.2 s | 100 / 96* / 100, avg 9.1 min |

\* All 36 hidden tests passed. The 4 points were lost to a false alarm in the snooping check; see [results](docs/results.md#hard-canary-on-basalt-5090-3-runs-each).

**Recommendation: Q2_0 on Basalt for the 5090.**
- Basalt is about 1.5× faster than Strata for Q2_0, and about 1.3× faster for IQ3_S, with the same coding results.
- Q2_0 matched IQ3_S on every coding test and finished sooner.
- If you'd rather have more accuracy headroom, use IQ3_S on Basalt. It writes output almost as fast as Q2_0 did on Strata, and reads prompts faster. Basalt's own measurements put IQ3_S closer to the full-precision model than Q2_0 (top-token agreement with Q8_0: 87% vs 78%).

Caveat: the coding tests cover small Python projects with up to about 85k tokens of context. Very long contexts and large codebases weren't tested.

Full numbers and discussion: **[docs/results.md](docs/results.md)**.

## What the tests are

- **Speed canary.** Cold 4k and 32k prompts, plus a follow-up on a 32k conversation. It records tokens per second, time to first token, and GPU memory and power.
- **Quality canary.** A three-turn opencode session that builds and fixes a log analyzer, graded by 9 hidden tests.
- **Hard canary.** A four-turn opencode session on a payment reconciler, graded by 36 hidden tests:
  - decisions are buried in a 64k-token transcript, one of them reversed later;
  - every amount must be exact to the cent;
  - later rule changes interact with earlier ones;
  - it ends with a refactor and an audit.

How they work, how they're scored, and how they're isolated: **[docs/methodology.md](docs/methodology.md)**.

## Quick start

You need Linux with `bwrap`, `socat` and Python 3.11+, plus Strata or Basalt, opencode 2.1.4 and the model files. Details are in **[docs/setup.md](docs/setup.md)**.

```sh
# Copy and adapt a server config (model and pack paths)
mkdir -p .strata && cp configs/5090/qwen-q2_0.json .strata/

# Run the hard coding test three times on that version
REPS=3 bench/run_hard.sh q2_0

# Or the shorter quality canary
bench/run_quality.sh q2_0

# Basalt on the 5090: speed test plus three hard runs per version
REPS=3 bench/run_basalt.sh q2_0 iq3_s
```

Each run writes a `report.json` with the score, the result of every hidden test, and timings.

## Repository layout

| Path | What's there |
|---|---|
| [`bench/`](bench) | the canaries, the sandbox, and the run drivers (Strata and Basalt on the 5090, Strata on the Strix) |
| [`bench/fixture/`](bench/fixture) | the starting projects the model works on |
| [`bench/hard_oracle/`](bench/hard_oracle) | hidden tests for the hard canary |
| [`bench/hard_reference/`](bench/hard_reference) | reference solution that shows the hard canary is passable |
| [`configs/`](configs) | Strata and Basalt server configs used on each machine |
| [`results/`](results) | every run's report and logs; Basalt runs are in [`results/basalt`](results/basalt) |
| [`docs/`](docs) | [results](docs/results.md), [methodology](docs/methodology.md), [setup](docs/setup.md) |

Run folders leave out bulky files: the model's copy of the project, raw opencode streams and the long prompts. Add `--print-prompt` to a `bench/hard_canary.py` command to print the long prompt again.

## Related

- [llm-coding-benchmark](https://github.com/akitaonrails/llm-coding-benchmark), a larger coding benchmark across local and cloud models.
