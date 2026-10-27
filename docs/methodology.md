# Methodology

## Speed canary

[`bench/canary.py`](../bench/canary.py) starts the model server itself (the command after `--`), waits until it is ready, and sends three requests, each generating 256 tokens:

| Workload | What it measures |
|---|---|
| cold-4k | a fresh 4k-token prompt |
| cold-32k | a fresh 32k-token prompt: prompt reading speed and time to first token |
| append-32k | a follow-up on the same 32k conversation, reusing the conversation cache |

It records time to first token, prompt tokens per second and output tokens per second. While the requests run, it also samples GPU memory, utilization, power and temperature, and system memory. GPU readings come from `nvidia-smi`, or from AMD sysfs when that isn't available, as on the Strix. Output goes to `report.json`, `gpu.csv`, `system.csv` and `server.log`.

## Coding canaries

Both coding tests drive [opencode](https://opencode.ai) 2.1.4 in non-interactive mode:
- flags: `opencode run --format json --auto`;
- one opencode session that continues across turns;
- the model server reached through its OpenAI-compatible API.

### Quality canary

[`bench/quality_canary.py`](../bench/quality_canary.py) uses the fixture in [`bench/fixture/logstats`](../bench/fixture/logstats), a streaming JSONL request-log analyzer with a buggy draft implementation.

1. Implement `docs/spec.md`, following decisions in a 48k-token "prior session" transcript.
2. Fix three reported bugs.
3. Requirements change: exclude `/health`, switch to nearest-rank p95, use bounded memory. Add regression tests and audit the diff.

### Hard canary

[`bench/hard_canary.py`](../bench/hard_canary.py) uses the fixture in [`bench/fixture/ledger`](../bench/fixture/ledger), a payment-event reconciler with charges, refunds, fees, currency conversion and a time window. It starts from a draft that uses floats and has the wrong rules.

1. **Implement the spec.** The first prompt is a 64k-token transcript (about 83k tokens with opencode's own system prompt). It contains:
   - **five binding decisions placed at fixed depths:** case-insensitive account ids; ignore `test` events; USD-only fees; `house_` accounts left out of the total; and a later correction saying `test` event ids *are* reserved;
   - **rejected proposals as distractors:** half-up rounding, and fees in any currency.
2. **Unordered exporter.** Refunds must match their charge wherever it appears in the file. The refund limit is evaluated in timestamp order, with ties broken by file order.
3. **New features.** A `chargeback` event type that interacts with the refund limit, and a `--currency` report option.
4. **Refactor and audit.** Move conversion into `src/ledger/money.py`, then audit everything against the spec, the transcript and all earlier requests.

Several rules interact. For example, account ids are case-insensitive, and that also applies when matching a refund's account to its charge and when spotting `house_` accounts. Money must be exact: three EUR 1.00 charges are $3.255, which must round half-even to $3.26. Floats give $3.25, and rounding each event gives $3.24.

The test was checked before any model ran it:
- the reference solution in [`bench/hard_reference`](../bench/hard_reference) scores 100;
- the untouched fixture passes 6 of the 36 hidden tests and scores 28.

## Scoring (both canaries)

| Part | Points | How |
|---|---|---|
| Functional correctness | 70 | share of hidden tests passed |
| Requirement retention | 20 | 4 points per check (below) |
| Engineering process | 10 | 4 if the model ran the tests, 6 if the final visible suite passes |

Requirement checks:
- **Both canaries:**
  - the supplied tests are unchanged;
  - no dependencies outside the standard library;
  - no attempt to reach the hidden tests in the model's logs.
- **Quality canary only:** deterministic JSON output, and bounded memory.
- **Hard canary only:** the model added regression tests, and no `float()` is used in the money code.

The hard canary also reports hidden-test results per category: `spec`, `context`, `turn2`, `turn3` and `turn4`. Its hidden tests are in [`bench/hard_oracle/test_hidden.py`](../bench/hard_oracle/test_hidden.py).

## Isolation

The model writes and runs code, so every turn and the grading run in a sandbox ([`bench/sandbox.py`](../bench/sandbox.py)):
- **No network:** bubblewrap with `--unshare-all` and `--clearenv`.
- **Limited view:** the sandbox sees `/usr`, the project at `/work`, a private home, and read-only bind mounts of the opencode binary, its config, and the prompt file when the prompt is long.
- **Model only:** the one way out is a Unix-socket gateway on the host that forwards `GET /v1/models` and `POST /v1/chat/completions` for the expected model, text only, and refuses everything else. Inside the sandbox, socat bridges `127.0.0.1:18080` to that socket.
- **Request log:** the gateway logs every model request (`provider-requests.jsonl`) with its timing, token usage and finish reason.
- **No other config:** opencode runs with project config, auto-update and model fetching disabled. The `task`, `webfetch` and `websearch` tools are denied.
- **Hidden tests:** bound in read-only, only for grading, after the model's last turn.
- **Contamination check:** the logs are scanned for paths that would show an attempt to find the hidden tests.

### Fixed gap: `ps` showed host paths

Until commit `1def71c`, `bwrap` itself ran as process 1 inside the sandbox. A model that ran `ps aux` saw its command line, which includes host paths such as the run folder. That revealed where the run lives but gave no access to it: the hidden tests aren't mounted until grading, and nothing outside the bind mounts is reachable.

It happened once, in Basalt IQ3_S run 2. The contamination check flagged the host paths in the log and cost the run 4 points, although the model never used them. All runs in [results](results.md) used the old sandbox, so they're comparable with each other.

The sandbox now passes `--as-pid-1`, so the agent's own shell is process 1 and `ps` shows only the agent's processes. Timeouts still stop everything inside: the harness kills `bwrap`, and `--die-with-parent` takes the rest down.

One smaller leak remains: `/proc/self/mountinfo` lists the host folders behind each bind mount. Reading it takes a deliberate look at kernel mount tables, and a run that does it is flagged by the same contamination check.
