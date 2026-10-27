"""Networkless bubblewrap sandbox for opencode runs, with a model-only gateway.

Same design as llm-non-coding-benchmark/scripts/run_local_baseline.py:
the sandbox has no network (--unshare-all) and sees only /usr, its work folder,
a private home and read-only files we bind in. The one way out is a Unix socket
to a gateway on the host that forwards GET /v1/models and POST
/v1/chat/completions for the expected model, and nothing else.
"""
import http.client
import http.server
import json
import os
from pathlib import Path
import socketserver
import threading
import time
import urllib.parse
from typing import Callable

# The native opencode binary (not the Node launcher), bound into the sandbox.
OPENCODE_BINARY = Path(os.environ.get("OPENCODE_BINARY") or Path.home() / (
    ".local/share/mise/installs/opencode/2.1.4/node_modules/.mise/"
    "@opencode+cli-linux-x64@2.1.4/node_modules/@opencode/cli-linux-x64/bin/opencode"
))
BRIDGE_PORT = 18080  # localhost port inside the sandbox, bridged to the gateway socket
MAX_TOKENS_CAP = 32768


class _UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    host: str
    port: int
    prefix: str
    model: str
    record: Callable[[dict], None]


class _GatewayHandler(http.server.BaseHTTPRequestHandler):
    server: _UnixServer  # pyright: ignore[reportIncompatibleVariableOverride]

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(403)
            return
        data = json.dumps({"object": "list", "data": [{"id": self.server.model, "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(403)
            return
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size <= 16_000_000:
            self.send_error(413)
            return
        payload = json.loads(self.rfile.read(size))
        if payload.get("model") != self.server.model:
            self.send_error(403)
            return
        # Text only: no image or audio URLs reach the model server.
        for message in payload.get("messages", []):
            content = message.get("content")
            if isinstance(content, list) and any(part.get("type") != "text" for part in content):
                self.send_error(403)
                return
        if payload.get("stream"):
            payload["stream_options"] = {"include_usage": True}
        started = time.monotonic()
        connection = http.client.HTTPConnection(self.server.host, self.server.port, timeout=3600)
        try:
            connection.request("POST", self.server.prefix + "/chat/completions", json.dumps(payload),
                               {"Content-Type": "application/json", "Authorization": "Bearer local"})
            response = connection.getresponse()
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.end_headers()
            received = bytearray()
            first_byte = None
            while chunk := response.read1(65536):
                if first_byte is None:
                    first_byte = time.monotonic() - started
                received.extend(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
            usage, finish, timings = None, None, None
            for line in received.decode(errors="replace").splitlines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                usage = event.get("usage") or usage
                timings = event.get("timings") or timings
                for choice in event.get("choices", []):
                    finish = choice.get("finish_reason") or finish
            self.server.record({
                "started_epoch": time.time() - (time.monotonic() - started),
                "seconds": time.monotonic() - started,
                "first_byte_seconds": first_byte,
                "http_status": response.status,
                "usage": usage,
                "finish_reason": finish,
                "timings": timings,
            })
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            connection.close()


class Gateway:
    """Unix-socket HTTP gateway that only forwards the expected model's chat requests."""

    def __init__(self, socket_path, base_url, model, metrics_path):
        url = urllib.parse.urlparse(base_url)
        self.server = _UnixServer(str(socket_path), _GatewayHandler)
        self.server.host, self.server.port = url.hostname or "127.0.0.1", url.port or 80
        self.server.prefix = url.path.rstrip("/")
        self.server.model = model
        lock = threading.Lock()

        def record(entry):
            with lock, open(metrics_path, "a", encoding="utf-8") as out:
                out.write(json.dumps(entry) + "\n")

        self.server.record = record
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def sandbox_command(command, *, work, home, ro_binds=(), env=None):
    """bwrap with no network: /usr, the work folder at /work, a private home, and the given read-only files."""
    cmd = [
        "bwrap", "--die-with-parent", "--unshare-all", "--new-session", "--as-pid-1",
        "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
        "--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
        "--bind", str(work), "/work", "--bind", str(home), "/home/bench",
    ]
    for source, target in ro_binds:
        cmd += ["--ro-bind", str(source), str(target)]
    cmd += ["--chdir", "/work", "--clearenv"]
    variables = {"HOME": "/home/bench", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb", "TMPDIR": "/tmp"}
    variables.update(env or {})
    for key, value in variables.items():
        cmd += ["--setenv", key, value]
    return cmd + ["--", *command]


def opencode_config(model, timeout_seconds):
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"bench/{model}",
        "small_model": f"bench/{model}",
        "enabled_providers": ["bench"],
        "share": "disabled",
        "autoupdate": False,
        "permission": {"*": "allow", "task": "deny", "webfetch": "deny", "websearch": "deny"},
        "provider": {
            "bench": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "bench",
                "options": {"baseURL": f"http://127.0.0.1:{BRIDGE_PORT}/v1", "apiKey": "local",
                            "timeout": int(timeout_seconds * 1000)},
                "models": {model: {"name": model, "tool_call": True, "reasoning": False,
                                   "limit": {"context": 131072, "output": MAX_TOKENS_CAP},
                                   "cost": {"input": 0, "output": 0}}},
            }
        },
    }


def opencode_command(args, *, work, home, socket_path, config_path, ro_binds=()):
    """Run opencode in the sandbox; socat bridges the in-sandbox localhost port to the gateway socket."""
    binds = [(OPENCODE_BINARY, "/opt/opencode"), (config_path, "/opt/config.json"),
             (socket_path, "/gateway.sock"), *ro_binds]
    env = {"OPENCODE_CONFIG": "/opt/config.json", "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
           "OPENCODE_DISABLE_AUTOUPDATE": "true", "OPENCODE_DISABLE_MODELS_FETCH": "true",
           "GIT_CEILING_DIRECTORIES": "/work"}
    shell = (f"socat TCP-LISTEN:{BRIDGE_PORT},bind=127.0.0.1,reuseaddr,fork UNIX-CONNECT:/gateway.sock & "
             'bridge=$!; trap "kill $bridge 2>/dev/null" EXIT; "$@"')
    return sandbox_command(["/bin/sh", "-c", shell, "bridge", "/opt/opencode", *args],
                           work=work, home=home, ro_binds=binds, env=env)
