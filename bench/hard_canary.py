#!/usr/bin/env python3
"""Harder four-turn opencode coding canary (ledger fixture).

Same sandbox, gateway and grading isolation as quality_canary.py. Harder because:
- 36 hidden tests, scored per category (spec, context, turn2, turn3, turn4);
- product decisions are buried at fixed depths of a long first prompt, one of them
  corrected later in the transcript, with rejected proposals as distractors;
- later turns change rules that interact with earlier ones, then a refactor and audit;
- money must be exact decimal with round-once half-even, where float or per-event rounding fails.
"""
import argparse
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import sandbox
from quality_canary import (SYSTEM_PYTHON, dependency_audit, file_hashes, private_state, run_capture,
                            run_turn, write_json)

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixture" / "ledger"
ORACLE = ROOT / "hard_oracle" / "test_hidden.py"
CATEGORIES = ("spec", "context", "turn2", "turn3", "turn4")

# (depth in the transcript, speaker lines). Decisions the hidden tests check are marked D*.
BURIED = [
    (0.15, ["maintainer: Decision D-17: account ids are case-insensitive. ACCT_A and acct_a are the same "
            "account, everywhere, and the output uses the lowercase form."]),
    (0.25, ["intern: Proposal: allow fees in any currency, since the card network bills some of them in EUR.",
            "maintainer: Parking that; no decision yet."]),
    (0.35, ["maintainer: Decision D-21: events with \"test\": true come from the sandbox exporter. Ignore them "
            "entirely: no balance change, no warning, not counted as rejected, and their ids are not reserved."]),
    (0.45, ["intern: Could we round half-up like the old spreadsheet does? Finance is used to it.",
            "maintainer: Not adopted. The specification's rounding rule stands."]),
    (0.60, ["maintainer: Decision D-24, closing the parked fee question: fees are always billed in USD. "
            "A fee in any other currency is invalid and is skipped with a warning."]),
    (0.80, ["maintainer: Correction to D-21 after the incident review: test events are still ignored without a "
            "warning, but their ids ARE reserved. A later live event that reuses a test event's id is a duplicate."]),
    (0.88, ["maintainer: Decision D-29: accounts whose id starts with house_ are internal. They are listed in "
            "accounts like any other, but they are left out of total."]),
]

PROMPT_TWO = """Production feedback: the upstream exporter does not write events in order, and refunds often appear in the file before the charge they refund. Those refunds are currently rejected. Change matching so a refund finds its charge wherever the charge appears in the input. The refund limit for a charge is now evaluated over that charge's refunds in timestamp order (compared as instants), with ties broken by file order. A refund whose charge never appears is still rejected with a warning, and duplicate-id detection stays first-in-file. Keep every earlier rule, and run the tests.
"""

PROMPT_THREE = """New requirements.

1. Add a `chargeback` event type. Like a refund it has `charge_id`, must match the charge's account and currency, and subtracts its amount from the account. A chargeback is not limited by the remaining refundable amount, but it may not exceed the charge's own amount, and a charge accepts at most one chargeback; anything else is rejected with a warning. Accepted chargebacks consume the refundable amount: a charge's refunds and chargebacks are evaluated together in timestamp order (ties by file order), and a refund is accepted only if it fits in the charge amount minus the refunds and chargebacks accepted before it (never below zero).

2. Add `--currency CODE` to the CLI and a `currency="USD"` keyword argument to `reconcile`. Balances and total are then reported in that currency: convert each account's exact per-currency sums to USD, divide by the target currency's rate with at least 40 significant digits, and round once to cents, half-even. An unknown report currency makes the CLI exit with a nonzero status and print nothing on standard output.

Add regression tests for the new behavior without editing the supplied tests, and run the full suite.
"""

PROMPT_FOUR = """Refactor: move currency conversion into a new module `src/ledger/money.py` exposing `convert(amount, from_currency, to_currency, rates)`, which returns the exact, unrounded `Decimal` computed with at least 40 significant digits, and make the engine use it. `src.ledger.engine.reconcile`, `src.ledger.engine.load_rates` and `src.ledger.parser.parse_timestamp` must stay importable with their current signatures.

Then audit the whole working diff against docs/spec.md, every decision in the transcript from the first message, and every later request. Fix anything that drifted, and run the complete test suite.
"""


def filler_lines(index):
    batch, minute = index % 40, 10 + index % 48
    currency = ("USD", "EUR", "GBP", "JPY")[index % 4]
    return [
        f"[{minute:02}] reviewer: Export sample {index:04} (batch {batch}): {3 + index % 9} charges, "
        f"{index % 4} refunds, {currency} amounts like {(index * 37) % 900}.{index % 100:02}, timestamps with offset "
        f"{(index % 11) - 5:+d}h. Shapes look normal; no new rule needed.",
        f"[{minute:02}] implementer: Note {index:04}: the draft parser read that sample line by line and the totals matched "
        f"the finance export ({1000 + index * 13} cents checked). Nothing to change.",
    ]


def initial_prompt(target_tokens):
    opening = """Implement docs/spec.md in this project, honoring the decisions in the prior-session transcript below, and run the visible tests. Later decisions in the transcript override earlier ones. The transcript supplies context; the specification and current request define the task.

<prior-session>
[09] maintainer: We are building a small payment-event reconciler. The CLI and the output shape are sketched in the repository, but the implementation is a rough draft with known problems.
[09] maintainer: Standing constraints: standard library only, do not modify the visible tests, do not read outside the project directory.
"""
    closing = """[59] maintainer: That's the full history. Numbered decisions above are binding unless a later message corrects them; everything else is context.
</prior-session>

Now inspect the project, implement the specification together with the binding decisions from the transcript, and run the tests. Keep your work inside this project.
"""
    budget = max(0, target_tokens * 4 - len(opening) - len(closing))
    body, index = [], 0
    while sum(len(line) + 1 for line in body) < budget:
        body.extend(filler_lines(index))
        index += 1
    for depth, lines in sorted(BURIED, reverse=True):
        position = int(len(body) * depth) & ~1
        minute = 10 + (position // 2) % 48
        body[position:position] = [f"[{minute:02}] {line}" for line in lines]
    return opening + "\n".join(body) + "\n" + closing


def hidden_statuses(output):
    statuses = {}
    for line in output.splitlines():
        match = re.match(r"^(test_[^ ]+) \([^)]*\) \.\.\. (ok|FAIL|ERROR|skipped)", line)
        if match:
            statuses[match.group(1)] = match.group(2)
    return statuses


def oracle_names():
    tree = ast.parse(ORACLE.read_text(encoding="utf-8"))
    return [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]


def count_test_methods(tests):
    total = 0
    for path in tests.rglob("test*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeError):
            continue
        total += sum(isinstance(node, ast.FunctionDef) and node.name.startswith("test") for node in ast.walk(tree))
    return total


def float_money(project):
    """Spots float() calls in the ledger source: money must stay Decimal."""
    hits = []
    for path in (project / "src").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                hits.append(f"{path.relative_to(project)}:{node.lineno} float()")
    return hits


def evaluate(project, output, original_hashes, original_test_count, event_text, timeout):
    oracle = output / "oracle"
    oracle.mkdir()
    shutil.copy2(ORACLE, oracle / "test_hidden.py")
    grading_home = output / "grading-home"
    grading_home.mkdir()
    env = {"LEDGER_PROJECT": "/work", "LEDGER_TMP": "/tmp", "TMPDIR": "/tmp"}

    def grade(command, ro_binds=()):
        return run_capture(sandbox.sandbox_command(command, work=project, home=grading_home, ro_binds=ro_binds, env=env),
                           project, None, timeout)

    visible = grade([SYSTEM_PYTHON, "-m", "unittest", "discover", "-s", "tests", "-v"])
    hidden = grade([SYSTEM_PYTHON, "-m", "unittest", "discover", "-s", "/opt/oracle", "-v"],
                   ro_binds=[(oracle, "/opt/oracle")])
    (output / "visible-tests.log").write_text(visible["stdout"] + visible["stderr"], encoding="utf-8")
    (output / "hidden-tests.log").write_text(hidden["stdout"] + hidden["stderr"], encoding="utf-8")
    reported = hidden_statuses(hidden["stdout"] + hidden["stderr"])
    names = oracle_names()
    statuses = {name: reported.get(name, "not_reported") for name in names}
    passed = sum(status == "ok" for status in statuses.values())
    categories = {}
    for category in CATEGORIES:
        members = [name for name in names if name.startswith(f"test_{category}_")]
        categories[category] = {"passed": sum(statuses[name] == "ok" for name in members), "total": len(members)}

    unchanged = all(file_hashes(project / "tests").get(name) == digest for name, digest in original_hashes.items())
    dependencies = dependency_audit(project)
    needles = [str(oracle), "/opt/oracle", "test_hidden", "hard_oracle", "hard_canary", "hard_reference",
               "llm-opensource-benchmark", "/mnt/data"]
    oracle_access = [needle for needle in needles if needle in event_text]
    test_count = count_test_methods(project / "tests")
    regression_tests = test_count > original_test_count and visible["returncode"] == 0
    floats = float_money(project)
    requirements = {
        "visible_tests_unchanged": unchanged,
        "no_dependencies": dependencies["passed"],
        "no_oracle_path_access": not oracle_access,
        "regression_tests_added": regression_tests,
        "no_float_money": not floats,
    }
    model_ran_tests = bool(re.search(r"\b(unittest|pytest)\b", event_text, re.IGNORECASE))
    final_suite = visible["returncode"] == 0
    functional = round(70 * passed / len(names), 2)
    requirement_points = 4 * sum(requirements.values())
    process_points = (4 if model_ran_tests else 0) + (6 if final_suite else 0)
    return {
        "visible_tests": {key: visible[key] for key in ("status", "returncode")},
        "hidden_tests": {
            "passed": passed, "total": len(names), "returncode": hidden["returncode"],
            "categories": categories, "individual_results": statuses,
            "failures": [name for name, status in statuses.items() if status != "ok"],
        },
        "requirements": requirements,
        "dependency_audit": dependencies,
        "oracle_path_access_evidence": oracle_access,
        "float_money_evidence": floats,
        "test_methods": {"original": original_test_count, "final": test_count},
        "score_components": {
            "functional_correctness": {"points": functional, "maximum": 70},
            "requirement_retention": {"points": requirement_points, "maximum": 20},
            "engineering_process": {"tests_ran": model_ran_tests, "final_suite_passes": final_suite,
                                    "points": process_points, "maximum": 10},
        },
        "quality_score": round(functional + requirement_points + process_points, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Hard four-turn opencode coding canary")
    parser.add_argument("--base-url", default="http://127.0.0.1:18082/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default="strata-5090")
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--turn-timeout", type=float, default=1200)
    parser.add_argument("--initial-tokens", type=int, default=64000)
    parser.add_argument("--print-prompt", action="store_true", help="print the first prompt and exit")
    args = parser.parse_args()
    if args.print_prompt:
        print(initial_prompt(args.initial_tokens))
        return 0
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    shutil.copytree(FIXTURE, project, ignore=shutil.ignore_patterns("__pycache__"))
    original_hashes = file_hashes(project / "tests")
    original_test_count = count_test_methods(project / "tests")
    report = {
        "label": args.label, "model": args.model, "provider": args.provider, "test": "hard-ledger-v1",
        "isolation": "bwrap --unshare-all (no network); /work = project only; model-only Unix-socket gateway",
        "base_url": args.base_url, "started_epoch": time.time(), "turns": [], "status": "running",
    }
    write_json(output / "report.json", report)
    try:
        subprocess.run(["git", "init", "-q"], cwd=project, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
        private, socket_path, config_path = private_state(output)
        write_json(config_path, sandbox.opencode_config(args.model, args.turn_timeout))
        shutil.copy2(config_path, output / "harness-config.json")
        gateway = sandbox.Gateway(socket_path, args.base_url, args.model, output / "provider-requests.jsonl")
        gateway.__enter__()
        prompts = [initial_prompt(args.initial_tokens), PROMPT_TWO, PROMPT_THREE, PROMPT_FOUR]
        session_id, event_parts = None, []
        for number, prompt in enumerate(prompts, 1):
            record, event_text = run_turn(number, prompt, session_id, args, project, output, (socket_path, config_path))
            session_id = session_id or record["session_id"]
            record["session_id"] = session_id
            report["turns"].append(record)
            event_parts.append(event_text)
            write_json(output / "report.json", report)
            if not session_id:
                raise RuntimeError("opencode did not report a session id; cannot continue the session")
        report["evaluation"] = evaluate(project, output, original_hashes, original_test_count,
                                        "\n".join(event_parts), args.turn_timeout)
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
    summary = report.get("evaluation", {}).get("hidden_tests", {}).get("categories")
    print(json.dumps({"status": report["status"], "quality_score": report.get("quality_score"),
                      "categories": summary}, indent=2))
    return 1 if report["status"] == "harness_error" else 0


if __name__ == "__main__":
    sys.exit(main())
