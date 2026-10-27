# Results

All runs used Strata as the server. The 5090 numbers are from Strata control 0.1.51 with engine 0.1.50 (CUDA); the Strix Halo numbers are from 0.1.45.3 `hip-halo`. Raw reports are in [`results/`](../results). Each run folder has a `report.json`.

## Speed

From the [speed canary](methodology.md#speed-canary). Ranges are across the cold-4k, cold-32k and append-32k workloads. "Reading" is prompt processing on the cold 32k prompt, and "first token" is time to first token on that same prompt.

### RTX 5090 (4 GB of VRAM kept free)

| Version | Output tok/s | Reading tok/s | First token | Report |
|---|---|---|---|---|
| Q2_0 | 245–270 | 6,200 | 7 s | [`qwen-q2_0-speed-elastic`](../results/qwen-q2_0-speed-elastic) |
| IQ3_XXS | 200–240 | 5,100 | 8 s | [`qwen-iq3_xxs-speed-elastic`](../results/qwen-iq3_xxs-speed-elastic) |
| IQ3_S | 160–210 | 6,200 | 7 s | [`qwen-iq3_s-speed-elastic`](../results/qwen-iq3_s-speed-elastic) |
| UD-Q4_K_XL | 44–52 | 2,300 | 19 s | [`qwen-q4_k_xl-speed-elastic`](../results/qwen-q4_k_xl-speed-elastic) |

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

Both versions passed every category on every run:
- the specification;
- the decisions buried in the long prompt, including the one corrected later;
- the out-of-order change;
- chargebacks and report currency;
- the refactor.

They also needed a similar number of failing test runs along the way. The difference is time: Q2_0 averaged 7.1 minutes, IQ3_S 12.0.

## Conclusions

- **Q2_0 is the best pick for opencode on the 5090.** It's the fastest version, and no test found it producing worse code than IQ3_S.
- **IQ3_S isn't worse, just slower.** On these tests, its extra precision bought nothing measurable.
- **UD-Q4_K_XL isn't practical on either machine** with this setup.
- **The Strix Halo is usable but slow for agent work.** It writes 50–65 tok/s, but takes about 2 minutes to read a 32k prompt.

**Limits of this evidence:**
- It covers one kind of task: small Python projects with up to about 85k tokens of context.
- Each version ran three times at most.
- 2-bit versions usually lose quality first on very long contexts (over 100k tokens) and on larger codebases, and neither was tested here.
