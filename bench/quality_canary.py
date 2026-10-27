#!/usr/bin/env python3
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import tempfile
import tomllib

import sandbox


ORACLE_TESTS = [
    "test_basic_count_errors_and_bytes",
    "test_malformed_lines_warn_and_continue",
    "test_empty_input",
    "test_timezone_offsets",
    "test_since_inclusive_until_exclusive",
    "test_health_excluded",
    "test_nearest_rank_p95",
    "test_deterministic_json_key_order",
    "test_bounded_memory_streaming",
]

ORACLE_SOURCE = r'''
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
import unittest

PROJECT = Path(os.environ["LOGSTATS_PROJECT"]).resolve()
sys.path.insert(0, str(PROJECT))

from src.logstats.parser import iter_records, parse_timestamp
from src.logstats.stats import summarize


def line(timestamp="2025-01-01T00:00:00Z", path="/api", status=200, size=10, latency=10):
    return json.dumps({
        "timestamp": timestamp, "path": path, "status": status,
        "bytes": size, "latency_ms": latency,
    }) + "\n"


def summarize_text(text, since=None, until=None):
    since = parse_timestamp(since) if since else None
    until = parse_timestamp(until) if until else None
    return summarize(iter_records(io.StringIO(text)), since=since, until=until)


class HiddenQualityTests(unittest.TestCase):
    def run_cli(self, text, *arguments):
        return subprocess.run(
            [sys.executable, "-m", "src.logstats.cli", "-", *arguments],
            cwd=PROJECT, input=text, text=True, capture_output=True, check=True,
        )

    def test_basic_count_errors_and_bytes(self):
        result = summarize_text(line(size=100, latency=10) + line(status=503, size=50, latency=20))
        self.assertEqual(result, {"count": 2, "bytes": 150, "error_rate": 0.5, "p95_ms": 20})

    def test_malformed_lines_warn_and_continue(self):
        text = line() + "{broken\n" + "[]\n" + '{"timestamp":"bad"}\n' + line(size=7)
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            result = summarize_text(text)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["bytes"], 17)
        self.assertGreaterEqual(errors.getvalue().count("warning"), 3)

    def test_empty_input(self):
        self.assertEqual(
            summarize_text(""),
            {"count": 0, "bytes": 0, "error_rate": 0.0, "p95_ms": None},
        )

    def test_timezone_offsets(self):
        text = line(timestamp="2025-01-01T00:30:00+01:00", size=1)
        text += line(timestamp="2024-12-31T19:30:00-05:00", size=2)
        result = summarize_text(
            text, since="2025-01-01T00:15:00Z", until="2025-01-01T01:00:00Z"
        )
        self.assertEqual((result["count"], result["bytes"]), (1, 2))

    def test_since_inclusive_until_exclusive(self):
        text = line(timestamp="2025-01-01T00:00:00Z", size=1)
        text += line(timestamp="2025-01-01T00:30:00Z", size=2)
        text += line(timestamp="2025-01-01T01:00:00Z", size=4)
        result = summarize_text(
            text, since="2025-01-01T00:00:00Z", until="2025-01-01T01:00:00Z"
        )
        self.assertEqual((result["count"], result["bytes"]), (2, 3))

    def test_health_excluded(self):
        text = line(path="/health", size=100, status=503)
        text += line(path="/healthz", size=5)
        text += line(path="/api", size=7)
        result = summarize_text(text)
        self.assertEqual((result["count"], result["bytes"], result["error_rate"]), (2, 12, 0.0))

    def test_nearest_rank_p95(self):
        result = summarize_text("".join(line(latency=value) for value in range(1, 21)))
        self.assertEqual(result["p95_ms"], 19)
        self.assertEqual(summarize_text("")["p95_ms"], None)

    def test_deterministic_json_key_order(self):
        text = line(size=100) + line(status=500, size=50, latency=20)
        first = self.run_cli(text)
        second = self.run_cli(text)
        self.assertEqual(first.stdout, second.stdout)
        pairs = json.loads(first.stdout, object_pairs_hook=list)
        self.assertEqual([key for key, _ in pairs], ["bytes", "count", "error_rate", "p95_ms"])
        self.assertEqual(first.stdout, '{"bytes": 150, "count": 2, "error_rate": 0.5, "p95_ms": 20}\n')

    def test_bounded_memory_streaming(self):
        temporary_root = Path(os.environ["LOGSTATS_TMP"])
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            source = Path(directory) / "large.jsonl"
            with source.open("w", encoding="utf-8") as output:
                for index in range(60000):
                    output.write(line(latency=index % 60001))
            tracemalloc.start()
            with source.open(encoding="utf-8") as lines:
                result = summarize(iter_records(lines))
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertEqual(result["count"], 60000)
            self.assertLess(peak, 2000000, "summary memory grows with request count")


if __name__ == "__main__":
    unittest.main()
'''

PROMPT_TWO = """CI is still red. A malformed record aborts processing instead of being skipped with a warning, an empty stream reports a null error rate rather than 0.0, and an event exactly at the --until cutoff is incorrectly included. Fix these failures and run the tests. Do not change the public command-line interface or JSON field shape.
"""

PROMPT_THREE = """The product requirements have now changed: exclude records whose path is exactly /health, and compute p95 with nearest rank (the sorted item at one-based index ceil(0.95 * count)); interpolation is no longer accepted. Request processing must use bounded memory independent of the number of input records; the documented fixed latency domain allows a fixed-size histogram. Add regression tests for the new behavior without editing the supplied tests. Finally, audit the working diff against the original specification and all later requests, then run the complete test suite.
"""


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def stop_process_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def run_capture(command, cwd, env, timeout):
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    status = "completed"
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        status = "timeout"
        stop_process_group(process)
        stdout, stderr = process.communicate()
    return {
        "status": status if status == "timeout" else ("completed" if process.returncode == 0 else "failed"),
        "returncode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def nested_values(value, key):
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                yield item
            yield from nested_values(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from nested_values(item, key)


def parse_events(raw):
    session_id = None
    token_metrics = []
    invalid_lines = 0
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        for key in ("sessionID", "sessionId", "session_id"):
            for value in nested_values(event, key):
                if isinstance(value, str) and value:
                    session_id = value
                    break
            if session_id:
                break
        for tokens in nested_values(event, "tokens"):
            if isinstance(tokens, dict) and any(
                key in tokens for key in ("input", "output", "prompt_tokens", "completion_tokens")
            ):
                token_metrics.append(tokens)
    return session_id, token_metrics, invalid_lines


def initial_prompt(target_tokens):
    opening = """Implement docs/spec.md in this project, honoring the decisions in the prior-session transcript below, and run the visible tests. The transcript supplies context; the specification and current request define the task.

<prior-session>
[09:00] maintainer: We are building a small streaming JSONL request-log analyzer. The CLI and four-field JSON result are already sketched.
[09:01] maintainer: Critical constraints agreed before the interruption: --since is inclusive and --until is exclusive; timestamps with offsets compare as instants. JSON keys must be deterministic. Use no dependencies outside the standard library. Do not modify the visible tests. Do not read outside the project directory.
[09:02] implementer: I will preserve those constraints. The initial percentile interpolation is acceptable for now, and malformed lines need a warning rather than a fatal error.
[09:03] reviewer: Empty input still has a JSON result, with zero count and bytes, zero error rate, and null percentile.
"""
    filler = []
    index = 0
    filler_target = max(0, target_tokens * 4 - len(opening) - 2000)
    while len("\n".join(filler)) < filler_target:
        filler.append(
            f"[09:{10 + index % 45:02}] reviewer: Design note {index:04}: compared parser batch {index % 32} "
            f"with the previous draft; timestamp sample offset {(index % 11) - 5:+d} hours, "
            f"latency {(index * 37) % 900} ms, status {200 + index % 4 * 100}. "
            "The accounting draft remains unchanged, and the sample was useful only for checking normal record shapes."
        )
        filler.append(
            f"[09:{10 + index % 45:02}] implementer: Journal {index:04}: the prototype yielded one record at a time, "
            f"formatted byte total {1000 + index * 13}, and kept warning text on stderr. "
            "No new product decision was recorded for this sample."
        )
        index += 1
    closing = """[09:58] reviewer: The notes above are historical design context, not additional feature requests.
[09:59] maintainer: Resume from the checked-in partial implementation and verify the actual behavior with the supplied suite.
</prior-session>

Now inspect the project, implement the specification and the early transcript decisions, and run the tests. Keep your work inside this project.
"""
    transcript = opening + "\n".join(filler) + "\n" + closing
    return transcript


SYSTEM_PYTHON = "/usr/bin/python3"  # the interpreter visible inside the sandbox


def private_state(output):
    """Short-path private folder for the gateway socket and opencode config (Unix sockets cap paths at 108 bytes)."""
    private = Path(tempfile.mkdtemp(prefix="osb-"))
    config_path = private / "opencode.json"
    return private, private / "gateway.sock", config_path


def run_turn(number, prompt, session_id, args, project, output, gateway_paths):
    socket_path, config_path = gateway_paths
    command = ["run", "--standalone", "--auto", "--agent", "build", "--format", "json"]
    if session_id is None:
        command += ["--model", f"bench/{args.model}", "--title", f"bench-{args.label}"]
    else:
        command += ["--session", session_id]
    ro_binds = []
    # Linux caps a single argv string at 128 KiB, so long prompts go in an attached read-only file.
    if len(prompt.encode("utf-8")) > 64 * 1024:
        prompt_path = output / f"turn-{number}.prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        ro_binds.append((prompt_path, "/opt/prompt.md"))
        command += ["--file", "/opt/prompt.md"]
        prompt = "The attached file is the full message for this turn. Read all of it and do what it asks."
    command.append(prompt)
    home = output / "home"
    home.mkdir(exist_ok=True)
    metrics = output / "provider-requests.jsonl"
    requests_before = len(metrics.read_text().splitlines()) if metrics.exists() else 0
    sandboxed = sandbox.opencode_command(command, work=project, home=home, socket_path=socket_path,
                                         config_path=config_path, ro_binds=ro_binds)
    started = time.monotonic()
    result = run_capture(sandboxed, project, None, args.turn_timeout)
    elapsed = time.monotonic() - started
    stdout_path = output / f"turn-{number}.stdout.jsonl"
    stderr_path = output / f"turn-{number}.stderr.log"
    stdout_path.write_text(result["stdout"], encoding="utf-8")
    stderr_path.write_text(result["stderr"], encoding="utf-8")
    requests = [json.loads(line) for line in (metrics.read_text().splitlines() if metrics.exists() else [])][requests_before:]
    observed_session, token_metrics, invalid_lines = parse_events(result["stdout"])
    return {
        "turn": number,
        "status": result["status"],
        "returncode": result["returncode"],
        "elapsed_seconds": elapsed,
        "command": command[:-1],
        "session_id": observed_session or session_id,
        "tokens": token_metrics[-1] if token_metrics else None,
        "token_events": token_metrics,
        "provider_requests": len(requests),
        "provider_seconds": round(sum(item.get("seconds") or 0 for item in requests), 2),
        "invalid_json_lines": invalid_lines,
        "stdout": str(stdout_path.relative_to(output)),
        "stderr": str(stderr_path.relative_to(output)),
    }, result["stdout"] + "\n" + result["stderr"]


def file_hashes(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        # Bytecode caches are build artifacts, not test sources.
        if path.is_file() and "__pycache__" not in path.parts
    }


def dependency_audit(project):
    violations = []
    local_names = {path.stem for path in project.rglob("*.py")} | {"src", "logstats"}
    for path in project.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError) as error:
            violations.append(f"{path.relative_to(project)}: {error}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name not in sys.stdlib_module_names and name not in local_names:
                    violations.append(f"{path.relative_to(project)} imports {name}")
    pyproject = project / "pyproject.toml"
    if pyproject.exists():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project_data = data.get("project", {})
        if project_data.get("dependencies") or project_data.get("optional-dependencies"):
            violations.append("pyproject.toml declares dependencies")
    for path in project.glob("requirements*.txt"):
        if any(line.strip() and not line.lstrip().startswith("#") for line in path.read_text().splitlines()):
            violations.append(f"{path.name} declares dependencies")
    return {"passed": not violations, "violations": violations}


def parse_hidden_results(output):
    statuses = {name: "not_reported" for name in ORACLE_TESTS}
    for line in output.splitlines():
        match = re.match(r"^(test_[^ ]+) \([^)]*\) \.\.\. (ok|FAIL|ERROR|skipped)", line)
        if match and match.group(1) in statuses:
            statuses[match.group(1)] = match.group(2)
    return statuses


def evaluate(project, output, original_hashes, event_text, timeout):
    oracle = output / "oracle"
    oracle.mkdir()
    (oracle / "test_quality.py").write_text(textwrap.dedent(ORACLE_SOURCE).lstrip(), encoding="utf-8")
    # The model's code runs during grading too, so grading uses the same networkless sandbox.
    # The oracle is bound read-only and only now, after the model's last turn.
    grading_home = output / "grading-home"
    grading_home.mkdir()
    grading_env = {"LOGSTATS_PROJECT": "/work", "LOGSTATS_TMP": "/tmp", "TMPDIR": "/tmp"}

    def grade(command, ro_binds=()):
        return run_capture(
            sandbox.sandbox_command(command, work=project, home=grading_home, ro_binds=ro_binds, env=grading_env),
            project, None, timeout,
        )

    visible = grade([SYSTEM_PYTHON, "-m", "unittest", "discover", "-s", "tests", "-v"])
    hidden = grade([SYSTEM_PYTHON, "-m", "unittest", "discover", "-s", "/opt/oracle", "-v"],
                   ro_binds=[(oracle, "/opt/oracle")])
    (output / "visible-tests.log").write_text(visible["stdout"] + visible["stderr"], encoding="utf-8")
    (output / "hidden-tests.log").write_text(hidden["stdout"] + hidden["stderr"], encoding="utf-8")
    statuses = parse_hidden_results(hidden["stdout"] + hidden["stderr"])
    passed = sum(status == "ok" for status in statuses.values())
    final_hashes = file_hashes(project / "tests")
    unchanged = all(final_hashes.get(name) == digest for name, digest in original_hashes.items())
    dependencies = dependency_audit(project)
    oracle_needles = [str(oracle), "/opt/oracle", "oracle/test_quality.py", "/oracle/", "test_quality.py",
                      "quality_canary", "ORACLE_", "llm-opensource-benchmark", "/mnt/data"]
    oracle_access = [needle for needle in oracle_needles if needle in event_text]
    deterministic = statuses["test_deterministic_json_key_order"] == "ok"
    bounded = statuses["test_bounded_memory_streaming"] == "ok"
    requirements = {
        "deterministic_output": {"passed": deterministic, "points": 4 if deterministic else 0},
        "bounded_memory": {"passed": bounded, "points": 4 if bounded else 0},
        "no_dependencies": {"passed": dependencies["passed"], "points": 4 if dependencies["passed"] else 0},
        "visible_tests_unchanged": {"passed": unchanged, "points": 4 if unchanged else 0},
        "no_oracle_path_access": {"passed": not oracle_access, "points": 4 if not oracle_access else 0},
    }
    model_ran_tests = bool(re.search(r"\b(unittest|pytest)\b", event_text, re.IGNORECASE))
    final_suite = visible["returncode"] == 0 and hidden["returncode"] == 0
    functional_points = round(70 * passed / len(ORACLE_TESTS), 2)
    requirement_points = sum(item["points"] for item in requirements.values())
    process_points = (4 if model_ran_tests else 0) + (6 if final_suite else 0)
    return {
        "visible_tests": {key: visible[key] for key in ("status", "returncode")},
        "hidden_tests": {
            "status": hidden["status"],
            "returncode": hidden["returncode"],
            "passed": passed,
            "total": len(ORACLE_TESTS),
            "individual_results": statuses,
            "individual_failures": [
                {"test": name, "status": status}
                for name, status in statuses.items() if status != "ok"
            ],
        },
        "dependency_audit": dependencies,
        "visible_tests_unchanged": unchanged,
        "oracle_path_access_evidence": oracle_access,
        "model_test_execution_evidence": model_ran_tests,
        "score_components": {
            "functional_correctness": {"passed": passed, "total": len(ORACLE_TESTS), "points": functional_points, "maximum": 70},
            "requirement_retention": {"checks": requirements, "points": requirement_points, "maximum": 20},
            "engineering_process": {"tests_ran": model_ran_tests, "final_suite_passes": final_suite, "points": process_points, "maximum": 10},
        },
        "quality_score": round(functional_points + requirement_points + process_points, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Three-turn opencode coding-quality canary")
    parser.add_argument("--base-url", default="http://127.0.0.1:18082/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="strata-5090")
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--turn-timeout", type=float, default=600)
    parser.add_argument("--initial-tokens", type=int, default=48000)
    args = parser.parse_args()
    if args.turn_timeout <= 0 or args.initial_tokens <= 0:
        parser.error("--turn-timeout and --initial-tokens must be positive")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    fixture = Path(__file__).resolve().parent / "fixture" / "logstats"
    shutil.copytree(fixture, project)
    original_hashes = file_hashes(project / "tests")
    report = {
        "label": args.label,
        "model": args.model,
        "provider": args.provider,
        "isolation": "bwrap --unshare-all (no network); /work = project only; model-only Unix-socket gateway",
        "base_url": args.base_url,
        "started_epoch": time.time(),
        "turns": [],
        "status": "running",
    }
    write_json(output / "report.json", report)
    try:
        # A private git repo in the project; inside the sandbox /work has no parent repo to find.
        subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        private, socket_path, config_path = private_state(output)
        write_json(config_path, sandbox.opencode_config(args.model, args.turn_timeout))
        shutil.copy2(config_path, output / "harness-config.json")
        gateway = sandbox.Gateway(socket_path, args.base_url, args.model, output / "provider-requests.jsonl")
        gateway.__enter__()
        prompts = [initial_prompt(args.initial_tokens), PROMPT_TWO, PROMPT_THREE]
        session_id = None
        event_parts = []
        for number, prompt in enumerate(prompts, 1):
            record, event_text = run_turn(number, prompt, session_id, args, project, output,
                                          (socket_path, config_path))
            if session_id is None:
                session_id = record["session_id"]
            if not session_id:
                report["turns"].append(record)
                write_json(output / "report.json", report)
                raise RuntimeError("opencode did not report a session id; cannot continue the session")
            record["session_id"] = session_id
            report["turns"].append(record)
            event_parts.append(event_text)
            write_json(output / "report.json", report)
        report["evaluation"] = evaluate(
            project, output, original_hashes, "\n".join(event_parts), args.turn_timeout
        )
        report["quality_score"] = report["evaluation"]["quality_score"]
        report["status"] = "completed"
    except Exception as error:
        report["status"] = "harness_error"
        report["error"] = repr(error)
    finally:
        if "gateway" in locals():
            gateway.__exit__(None, None, None)
        if "private" in locals():
            shutil.rmtree(private, ignore_errors=True)
    report["finished_epoch"] = time.time()
    write_json(output / "report.json", report)
    print(json.dumps({"status": report["status"], "quality_score": report.get("quality_score")}, indent=2))
    return 1 if report["status"] == "harness_error" else 0


if __name__ == "__main__":
    sys.exit(main())
