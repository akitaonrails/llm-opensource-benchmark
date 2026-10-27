#!/usr/bin/env python3
import argparse
import csv
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request


def monotonic():
    return time.monotonic()


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def synthetic_prompt(target_tokens):
    lines = []
    index = 0
    while len("\n".join(lines)) < target_tokens * 4:
        lines.append(
            f"Record {index:05}: service=api-{index % 17:02}; "
            f"request={index * 7919:08}; latency_ms={(index * 37) % 900}; "
            "inspect the deployment journal and preserve the accounting constraints."
        )
        index += 1
    return "\n".join(lines)


def request_json(url, method="GET", body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class GpuMonitor:
    def __init__(self, output_path, reserve_mib, stop_event, violation_event):
        self.output_path = output_path
        self.reserve_mib = reserve_mib
        self.stop_event = stop_event
        self.violation_event = violation_event
        self.low_samples = 0
        self.samples = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def read_gpu(self):
        """(used MiB, free MiB, utilization %, power W, temperature C) from nvidia-smi, or AMD sysfs without it."""
        if shutil.which("nvidia-smi"):
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.free,utilization.gpu,power.draw,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return [field.strip() for field in result.stdout.strip().split(",")]
        device = Path("/sys/class/drm/card0/device")
        used = int((device / "mem_info_vram_used").read_text()) / 1048576
        total = int((device / "mem_info_vram_total").read_text()) / 1048576
        busy = (device / "gpu_busy_percent").read_text().strip()
        hwmon = next(iter(sorted((device / "hwmon").glob("hwmon*"))), None)
        power = temperature = ""
        if hwmon is not None:
            for name in ("power1_average", "power1_input"):
                if (hwmon / name).exists():
                    power = f"{int((hwmon / name).read_text()) / 1e6:.2f}"
                    break
            if (hwmon / "temp1_input").exists():
                temperature = str(int((hwmon / "temp1_input").read_text()) // 1000)
        return [f"{used:.0f}", f"{total - used:.0f}", busy, power, temperature]

    def run(self):
        with self.output_path.open("w", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["epoch", "used_mib", "free_mib", "utilization", "power_w", "temperature_c"])
            while not self.stop_event.is_set():
                try:
                    fields = self.read_gpu()
                    row = [time.time(), *fields]
                    writer.writerow(row)
                    output.flush()
                    free_mib = float(fields[1])
                    self.samples.append({"epoch": row[0], "free_mib": free_mib})
                    if free_mib < self.reserve_mib - 512:
                        self.low_samples += 1
                    else:
                        self.low_samples = 0
                    if self.low_samples >= 2:
                        self.violation_event.set()
                        return
                except Exception as error:
                    writer.writerow([time.time(), "ERROR", str(error)])
                    output.flush()
                self.stop_event.wait(1.0)

    def summary(self):
        if not self.samples:
            return {"samples": 0}
        return {
            "samples": len(self.samples),
            "minimum_free_mib": min(sample["free_mib"] for sample in self.samples),
            "maximum_free_mib": max(sample["free_mib"] for sample in self.samples),
            "safety_violation": self.violation_event.is_set(),
        }


class SystemMonitor:
    def __init__(self, output_path, stop_event):
        self.output_path = output_path
        self.stop_event = stop_event
        self.samples = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        previous = None
        with self.output_path.open("w", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(["epoch", "available_mib", "swap_used_mib", "nvme_read_mib_s", "nvme_write_mib_s"])
            while not self.stop_event.is_set():
                now = monotonic()
                meminfo = {}
                for line in Path("/proc/meminfo").read_text().splitlines():
                    key, value = line.split(":", 1)
                    meminfo[key] = int(value.strip().split()[0]) / 1024
                read_sectors = 0
                write_sectors = 0
                for line in Path("/proc/diskstats").read_text().splitlines():
                    fields = line.split()
                    if fields[2] == "nvme0n1":
                        read_sectors = int(fields[5])
                        write_sectors = int(fields[9])
                        break
                current = (now, read_sectors, write_sectors)
                read_rate = 0.0
                write_rate = 0.0
                if previous is not None and now > previous[0]:
                    read_rate = (read_sectors - previous[1]) * 512 / 1048576 / (now - previous[0])
                    write_rate = (write_sectors - previous[2]) * 512 / 1048576 / (now - previous[0])
                previous = current
                sample = {
                    "epoch": time.time(),
                    "available_mib": meminfo["MemAvailable"],
                    "swap_used_mib": meminfo["SwapTotal"] - meminfo["SwapFree"],
                    "nvme_read_mib_s": read_rate,
                    "nvme_write_mib_s": write_rate,
                }
                self.samples.append(sample)
                writer.writerow(sample.values())
                output.flush()
                self.stop_event.wait(1.0)

    def summary(self):
        if not self.samples:
            return {"samples": 0}
        return {
            "samples": len(self.samples),
            "minimum_available_mib": min(sample["available_mib"] for sample in self.samples),
            "maximum_swap_used_mib": max(sample["swap_used_mib"] for sample in self.samples),
            "swap_growth_mib": self.samples[-1]["swap_used_mib"] - self.samples[0]["swap_used_mib"],
            "maximum_nvme_read_mib_s": max(sample["nvme_read_mib_s"] for sample in self.samples),
        }


def stream_completion(base_url, model, messages, max_tokens, timeout):
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
        "ignore_eos": True,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = monotonic()
    first = None
    previous = None
    gaps = []
    events = []
    usage = {}
    finish_reasons = []
    text_parts = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            events.append({"received": monotonic() - started, "event": event})
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices", []):
                if choice.get("finish_reason"):
                    finish_reasons.append(choice["finish_reason"])
                delta = choice.get("delta", {})
                content = delta.get("content") or delta.get("reasoning") or delta.get("reasoning_content")
                if content or delta.get("tool_calls"):
                    now = monotonic()
                    if first is None:
                        first = now
                    elif previous is not None:
                        gaps.append(now - previous)
                    previous = now
                    if content:
                        text_parts.append(content)
    elapsed = monotonic() - started
    completion_tokens = usage.get("completion_tokens", 0)
    prompt_tokens = usage.get("prompt_tokens", 0)
    ttft = None if first is None else first - started
    decode_seconds = None if first is None else elapsed - ttft
    return {
        "request": body,
        "elapsed_seconds": elapsed,
        "ttft_seconds": ttft,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prompt_tokens_per_second": prompt_tokens / ttft if ttft else None,
        "decode_tokens_per_second": (completion_tokens - 1) / decode_seconds if decode_seconds and completion_tokens > 1 else None,
        "tpot_p50_seconds": percentile(gaps, 0.50),
        "tpot_p95_seconds": percentile(gaps, 0.95),
        "tpot_p99_seconds": percentile(gaps, 0.99),
        "finish_reasons": finish_reasons,
        "usage": usage,
        "text": "".join(text_parts),
        "events": events,
    }


def stop_process_group(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description="Short local inference speed and memory admission canary")
    parser.add_argument("--base-url", default="http://127.0.0.1:18081")
    parser.add_argument("--model", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--ready-timeout", type=float, default=900)
    parser.add_argument("--request-timeout", type=float, default=900)
    parser.add_argument("--reserve-mib", type=int, default=4096)
    parser.add_argument("--dynamic-reserve", action="store_true")
    parser.add_argument("--server-log", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command[0] != "--":
        parser.error("provide the server command after --")
    command = args.command[1:]
    started_monotonic = monotonic()
    api_root = args.base_url.rstrip("/")
    health_url = api_root.removesuffix("/v1") + "/health"

    def apply_reserve():
        if args.dynamic_reserve:
            return request_json(api_root + "/vram", method="POST", body={"reserve_mib": args.reserve_mib}, timeout=30)
        return None

    args.output.mkdir(parents=True, exist_ok=False)
    server_log_path = args.server_log or args.output / "server.log"
    report = {
        "label": args.label,
        "model": args.model,
        "base_url": args.base_url,
        "command": command,
        "started_epoch": time.time(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "reserve_mib": args.reserve_mib,
        "status": "starting",
        "records": [],
    }
    write_json(args.output / "manifest.json", {key: value for key, value in report.items() if key != "records"})
    stop_monitor = threading.Event()
    safety_violation = threading.Event()
    monitor = GpuMonitor(args.output / "gpu.csv", args.reserve_mib, stop_monitor, safety_violation)
    system_monitor = SystemMonitor(args.output / "system.csv", stop_monitor)
    monitor.start()
    system_monitor.start()
    process = None
    try:
        with server_log_path.open("w") as server_log:
            process = subprocess.Popen(
                command,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            report["server_pid"] = process.pid
            deadline = monotonic() + args.ready_timeout
            while monotonic() < deadline:
                if safety_violation.is_set():
                    raise RuntimeError("GPU free-memory safety boundary crossed during startup")
                if process.poll() is not None:
                    raise RuntimeError(f"server exited during startup with status {process.returncode}")
                try:
                    request_json(health_url, timeout=5)
                    break
                except Exception:
                    time.sleep(2)
            else:
                raise TimeoutError("server did not become ready")
            report["ready_seconds"] = monotonic() - started_monotonic
            report["reserve_response"] = apply_reserve()
            prompt4 = synthetic_prompt(4096)
            prompt32 = synthetic_prompt(32768)
            workloads = [
                ("cold-4k", [{"role": "user", "content": prompt4 + "\nExplain the reconciliation algorithm in detail."}]),
                ("cold-32k", [{"role": "user", "content": prompt32 + "\nExplain the reconciliation algorithm in detail."}]),
                (
                    "append-32k",
                    [
                        {"role": "user", "content": prompt32 + "\nExplain the reconciliation algorithm in detail."},
                        {"role": "assistant", "content": "The earlier request is complete."},
                        {"role": "user", "content": "Now audit the boundary handling and describe the required fixes."},
                    ],
                ),
            ]
            for name, messages in workloads:
                if safety_violation.is_set():
                    raise RuntimeError("GPU free-memory safety boundary crossed")
                record = {"name": name, "started_epoch": time.time()}
                try:
                    record["reserve_response"] = apply_reserve()
                    record.update(stream_completion(args.base_url, args.model, messages, args.max_tokens, args.request_timeout))
                    record["status"] = "completed"
                except Exception as error:
                    record["status"] = "failed"
                    record["error"] = repr(error)
                    report["records"].append(record)
                    write_json(args.output / "report.json", report)
                    raise
                report["records"].append(record)
                write_json(args.output / "report.json", report)
            report["status"] = "completed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = repr(error)
    finally:
        if process is not None:
            stop_process_group(process)
        stop_monitor.set()
        monitor.thread.join(timeout=10)
        system_monitor.thread.join(timeout=10)
        report["gpu"] = monitor.summary()
        report["system"] = system_monitor.summary()
        report["finished_epoch"] = time.time()
        write_json(args.output / "report.json", report)
    print(json.dumps({"status": report["status"], "gpu": report["gpu"], "system": report["system"], "records": [
        {key: value for key, value in record.items() if key not in {"events", "request", "text", "usage"}}
        for record in report["records"]
    ]}, indent=2))
    return 0 if report["status"] == "completed" and not safety_violation.is_set() else 1


if __name__ == "__main__":
    sys.exit(main())
