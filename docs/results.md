# Results

Servers:
- **Strata:** control 0.1.51 with engine 0.1.50 (CUDA) on the 5090; 0.1.45.3 `hip-halo` on the Strix Halo.
- **Basalt:** commit `5554887` of [jesdga95/basalt](https://github.com/jesdga95/basalt), on the 5090 only, with its own packs from [jesdga/Qwen3.8-Flash-Next-Basalt](https://huggingface.co/jesdga/Qwen3.8-Flash-Next-Basalt).

Raw reports are in [`results/`](../results); Basalt runs are in [`results/basalt`](../results/basalt). Each run folder has a `report.json`.

## Speed

From the [speed canary](methodology.md#speed-canary). Ranges are across the cold-4k, cold-32k and append-32k workloads. "Reading" is prompt processing on the cold 32k prompt, and "first token" is time to first token on that same prompt.

### RTX 5090 (4 GB of VRAM kept free)

| Version | Output tok/s | Reading tok/s | First token | Report |
|---|---|---|---|---|
| Q2_0 | 245–270 | 6,200 | 7 s | [`qwen-q2_0-speed-elastic`](../results/qwen-q2_0-speed-elastic) |
| IQ3_XXS | 200–240 | 5,100 | 8 s | [`qwen-iq3_xxs-speed-elastic`](../results/qwen-iq3_xxs-speed-elastic) |
| IQ3_S | 160–210 | 6,200 | 7 s | [`qwen-iq3_s-speed-elastic`](../results/qwen-iq3_s-speed-elastic) |
| UD-Q4_K_XL | 44–52 | 2,300 | 19 s | [`qwen-q4_k_xl-speed-elastic`](../results/qwen-q4_k_xl-speed-elastic) |

### RTX 5090 on Basalt (4 GB of VRAM kept free)

Same canary, context size (131k) and int8 KV cache as the Strata runs. Basalt keeps the model's experts in system RAM (about 42 GB for IQ3_S) and holds about 23 GB on the GPU.

| Version | Output tok/s | Reading tok/s | First token | vs Strata output | Report |
|---|---|---|---|---|---|
| Q2_0 | **377–400** | 8,600 | 4.9 s | about 1.5× | [`basalt/q2_0-speed`](../results/basalt/q2_0-speed) |
| IQ3_S | 223–231 | 8,200 | 5.2 s | about 1.3× | [`basalt/iq3_s-speed`](../results/basalt/iq3_s-speed) |

Free VRAM stayed between 3.6 and 4.4 GB during the runs, with `vram-reserve-mib` set to 4608. The dips came from desktop apps growing, while Basalt's own use held steady.

### Strix Halo (whole GPU)

| Version | Output tok/s | Reading tok/s | First token | Report |
|---|---|---|---|---|
| Q2_0 | 55–65 | 371 | 114 s | [`strix/qwen-q2_0-speed`](../results/strix/qwen-q2_0-speed) |
| IQ3_XXS | 50–57 | 347 | 122 s | [`strix/qwen-iq3_xxs-speed`](../results/strix/qwen-iq3_xxs-speed) |
| IQ3_S | 47–51 | 338 | 126 s | [`strix/qwen-iq3_s-speed`](../results/strix/qwen-iq3_s-speed) |
| UD-Q4_K_XL | 17 (one 4k request) | 137 | — | [`strix/qwen-q4_k_xl-speed.crashed`](../results/strix/qwen-q4_k_xl-speed.crashed) |

**UD-Q4_K_XL doesn't fit either machine with this setup:**
- **5090:** it runs, but slowly. Its quality run was killed by `systemd-oomd`.
- **Strix:** free VRAM reached zero, the GPU started paging, and the machine hung until it was rebooted.

**DeepSeek V4.1** got a calibration run on the 5090 and was dropped at about 17 tok/s. Its reports are in `results/deepseek-v4.1-*`.

## Coding quality

Scores are out of 100. See [methodology](methodology.md) for how they're computed.

### Quality canary (3 turns, 9 hidden tests)

| Version | 5090 | Strix |
|---|---|---|
| Q2_0 | 100 | 100 |
| IQ3_XXS | 100 | 100 |
| IQ3_S | 100 | 100 |

Every version passed everything, so this test can't separate them. That's why the hard canary was built.

### Hard canary (4 turns, 36 hidden tests), 5090, 3 runs each

| Run | Score | Hidden tests | Time for 4 turns | Failing test runs before success | Output tokens |
|---|---|---|---|---|---|
| Q2_0 r1 | 100 | 36/36 | 5.3 min | 2 | 70k |
| Q2_0 r2 | 100 | 36/36 | 8.8 min | 10 | 117k |
| Q2_0 r3 | 100 | 36/36 | 7.4 min | 6 | 100k |
| IQ3_S r1 | 100 | 36/36 | 11.9 min | 2 | 110k |
| IQ3_S r2 | 100 | 36/36 | 13.3 min | 10 | 127k |
| IQ3_S r3 | 100 | 36/36 | 11.1 min | 4 | 98k |

Both versions passed every category on every Strata run:
- the specification;
- the decisions buried in the long prompt, including the one corrected later;
- the out-of-order change;
- chargebacks and report currency;
- the refactor.

They also needed a similar number of failing test runs along the way. The difference is time: Q2_0 averaged 7.1 minutes, IQ3_S 12.0.

### Hard canary on Basalt (5090, 3 runs each)

| Run | Score | Hidden tests | Time for 4 turns | Output tokens |
|---|---|---|---|---|
| Q2_0 r1 | 100 | 36/36 | 5.9 min | 109k |
| Q2_0 r2 | 100 | 36/36 | 4.2 min | 78k |
| Q2_0 r3 | 100 | 36/36 | 6.3 min | 116k |
| IQ3_S r1 | 100 | 36/36 | 5.8 min | 61k |
| IQ3_S r2 | 96 | 36/36 | 11.3 min | 121k |
| IQ3_S r3 | 100 | 36/36 | 10.1 min | 112k |

Every run passed all 36 hidden tests. Average time per 4-turn session:
- **Q2_0:** 5.5 min on Basalt, against 7.1 on Strata.
- **IQ3_S:** 9.1 min on Basalt, against 12.0 on Strata.

The time saved is smaller than the raw speed gain because each session also spends time running tests and tools, and output length varies a lot between runs (61k to 121k tokens).

**IQ3_S r2's 96 is a false alarm.** The model ran `ps aux` while exploring the sandbox. That listed the sandbox's own `bwrap` process, whose command line contains host paths, and the snooping check flagged them. The model never used those paths, and the hidden tests aren't mounted until grading. See [the known gap in methodology](methodology.md#known-gap).

## Conclusions

- **Q2_0 on Basalt is the best pick for opencode on the 5090.** It's the fastest combination, at about 390 tok/s, and no test found Q2_0 producing worse code than IQ3_S.
- **Basalt beats Strata on the 5090** for every version tested: faster output, faster prompt reading, and a shorter wait for the first token, with the same coding results.
- **IQ3_S on Basalt is the cautious option.** Its output speed (223–231 tok/s) is close to Q2_0 on Strata (245–270), and it reads prompts faster. Basalt's published measurements place it closer to the full-precision model: top-token agreement with Q8_0 is 87% for IQ3_S and 78% for Q2_0.
- **On these tests IQ3_S isn't worse, just slower.** Its extra precision bought nothing measurable here.
- **UD-Q4_K_XL isn't practical on either machine** with this setup.
- **The Strix Halo is usable but slow for agent work.** It writes 50–65 tok/s, but takes about 2 minutes to read a 32k prompt.

**Limits of this evidence:**
- It covers one kind of task: small Python projects with up to about 85k tokens of context.
- Each version ran three times at most per server.
- Basalt was tested only on the 5090.
- 2-bit versions usually lose quality first on very long contexts (over 100k tokens) and on larger codebases, and neither was tested here.
