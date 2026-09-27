"""Trace viewer: run index, run and step loaders, safe file lookup, HTTP routes, and server."""
from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

RUN_SCHEMA = "phone-lab.run.v1"
STEP_SCHEMA = "phone-lab.step.v1"
TREE_SCHEMA = "phone-lab.tree.v1"
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TRACE_UI_PATH = Path(__file__).resolve().parent / "ui" / "trace.html"

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class TraceError(Exception):
    """Trace domain error carrying an HTTP status code and safe message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def valid_run_id(run_id: str) -> bool:
    """True when run_id matches the RUN_ID pattern and is not '.' or '..'."""
    if not isinstance(run_id, str) or run_id in (".", ".."):
        return False
    return bool(RUN_ID.fullmatch(run_id))


def run_dir(runs_dir: Path, run_id: str) -> Path:
    """Resolve and validate the run directory under runs_dir."""
    if not valid_run_id(run_id):
        raise TraceError(400, f"invalid run_id: {run_id}")
    base = Path(runs_dir).resolve()
    candidate = (Path(runs_dir) / run_id).resolve()
    if not candidate.is_relative_to(base):
        raise TraceError(400, "run directory escapes runs dir")
    if not (candidate / "run.json").is_file():
        raise TraceError(404, f"run not found: {run_id}")
    return candidate


def list_runs(runs_dir: Path) -> list[dict]:
    """Index entries for all valid runs under runs_dir, sorted newest first."""
    runs_path = Path(runs_dir)
    if not runs_path.is_dir():
        return []

    runs: list[dict] = []
    for child in runs_path.iterdir():
        if not child.is_dir() or not valid_run_id(child.name):
            continue
        run_file = child / "run.json"
        if not run_file.is_file():
            continue
        try:
            data = json.loads(run_file.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict) or data.get("schema") != RUN_SCHEMA:
            continue

        trail_data = data.get("trail")
        trail_summary = None
        if isinstance(trail_data, dict):
            trail_summary = {
                "name": trail_data.get("name"),
                "step_count": trail_data.get("step_count"),
            }
        steps = data.get("steps")
        step_count = len(steps) if isinstance(steps, list) else 0

        runs.append({
            "run_id": data.get("run_id") or child.name,
            "kind": data.get("kind"),
            "created_at": data.get("created_at"),
            "finished_at": data.get("finished_at"),
            "device": data.get("device"),
            "trail": trail_summary,
            "result": data.get("result"),
            "timings": data.get("timings"),
            "step_count": step_count,
        })

    def _sort_key(entry: dict) -> tuple[float, str, str]:
        timings = entry.get("timings") or {}
        started_at = timings.get("started_at")
        t_val = float(started_at) if isinstance(started_at, (int, float)) else 0.0
        c_val = str(entry.get("created_at") or "")
        n_val = str(entry.get("run_id") or "")
        return (t_val, c_val, n_val)

    runs.sort(key=_sort_key, reverse=True)
    return runs


def load_run(runs_dir: Path, run_id: str) -> dict:
    """Parsed run.json plus step_dirs listing."""
    r_dir = run_dir(runs_dir, run_id)
    run_file = r_dir / "run.json"
    try:
        data = json.loads(run_file.read_text())
    except (ValueError, OSError) as exc:
        raise TraceError(400, f"corrupt run.json: {exc}")
    if not isinstance(data, dict) or data.get("schema") != RUN_SCHEMA:
        raise TraceError(400, "invalid run schema")

    steps_dir = r_dir / "steps"
    step_dirs: list[str] = []
    if steps_dir.is_dir():
        step_dirs = sorted([p.name for p in steps_dir.iterdir() if p.is_dir()])
    data["step_dirs"] = step_dirs
    return data


def load_step(runs_dir: Path, run_id: str, index: int) -> dict:
    """Parsed step.json or pending marker when missing/corrupt."""
    if index < 0:
        raise TraceError(400, f"invalid step index: {index}")
    r_dir = run_dir(runs_dir, run_id)
    step_dir = r_dir / "steps" / f"{index:03d}"
    if not step_dir.is_dir():
        step_dir = r_dir / "steps" / str(index)
        if not step_dir.is_dir():
            raise TraceError(404, f"step {index} not found")

    step_file = step_dir / "step.json"
    if not step_file.is_file():
        return {"schema": STEP_SCHEMA, "index": index, "pending": True}
    try:
        data = json.loads(step_file.read_text())
    except (ValueError, OSError):
        return {"schema": STEP_SCHEMA, "index": index, "pending": True}
    if not isinstance(data, dict):
        return {"schema": STEP_SCHEMA, "index": index, "pending": True}
    return data


def safe_file(runs_dir: Path, run_id: str, rel: str) -> Path:
    """Validate and return safe path inside the run directory."""
    if not valid_run_id(run_id):
        raise TraceError(400, f"invalid run_id: {run_id}")
    if not rel or not isinstance(rel, str):
        raise TraceError(400, "empty path")
    if rel.startswith("/") or rel.startswith("\\") or Path(rel).is_absolute():
        raise TraceError(400, "absolute path rejected")

    suffix = Path(rel).suffix.lower()
    if suffix not in (".png", ".json"):
        raise TraceError(400, f"invalid file suffix: {suffix}")

    segments = rel.replace("\\", "/").split("/")
    for seg in segments:
        if not seg or seg == ".." or not _SEGMENT_RE.fullmatch(seg):
            raise TraceError(400, f"invalid path segment: {seg}")

    r_dir = run_dir(runs_dir, run_id)
    target = (r_dir / rel).resolve()
    if not target.is_relative_to(r_dir.resolve()):
        raise TraceError(400, "path escapes run directory")
    if not target.is_file():
        raise TraceError(404, f"file not found: {rel}")
    return target


class ResponseMixin:
    """Shared response writers so headers cannot drift between the viewer and the trace server."""

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")


def handle_get(handler: ResponseMixin, runs_dir: Path, path: str) -> bool:
    """Route trace GET requests. Returns False when not a trace route."""
    try:
        if path == "/trace":
            if not TRACE_UI_PATH.is_file():
                handler._json({"error": "trace UI not found"}, 404)
            else:
                handler._send(200, TRACE_UI_PATH.read_bytes(), "text/html; charset=utf-8")
            return True

        if path == "/api/runs":
            runs = list_runs(runs_dir)
            handler._json(runs, 200)
            return True

        if path.startswith("/api/runs/"):
            rest = path[len("/api/runs/"):]
            parts = rest.split("/")
            if len(parts) == 1 and parts[0]:
                run_id = parts[0]
                handler._json(load_run(runs_dir, run_id), 200)
                return True
            if len(parts) == 3 and parts[0] and parts[1] == "steps" and parts[2]:
                run_id = parts[0]
                step_str = parts[2]
                if not re.fullmatch(r"\d+", step_str):
                    raise TraceError(400, f"invalid step index: {step_str}")
                index = int(step_str)
                handler._json(load_step(runs_dir, run_id, index), 200)
                return True
            return False

        if path.startswith("/runs/"):
            rest = path[len("/runs/"):]
            parts = rest.split("/", 1)
            if len(parts) == 2 and parts[0] and parts[1]:
                run_id, rel = parts[0], parts[1]
                target = safe_file(runs_dir, run_id, rel)
                content_type = "image/png" if target.suffix.lower() == ".png" else "application/json"
                handler._send(200, target.read_bytes(), content_type)
                return True
            return False

        return False

    except TraceError as exc:
        handler._json({"error": exc.message}, exc.status)
        return True
    except Exception as exc:
        handler.log_message("trace error on %s: %s", path, exc)
        handler._json({"error": "internal error"}, 500)
        return True


class TraceServer(ThreadingHTTPServer):
    """HTTP server dedicated to serving trace runs and UI."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], runs_dir: Path) -> None:
        super().__init__(address, TraceHandler)
        self.runs_dir = Path(runs_dir)


class TraceHandler(ResponseMixin, BaseHTTPRequestHandler):
    """Handler for trace-only server routes."""
    server_version = "phone-lab/0.1"
    server: TraceServer

    def log_message(self, fmt: str, *args) -> None:
        print(f"{time.strftime('%H:%M:%S')} {fmt % args}", flush=True)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/trace")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if not handle_get(self, self.server.runs_dir, path):
            self._json({"error": "not found"}, 404)


def serve_trace(host: str = "127.0.0.1", port: int = 8792,
                runs_dir: Path = Path("runs/phone-lab-runs")) -> None:
    """Run the trace viewer server until Ctrl-C."""
    server = TraceServer((host, port), Path(runs_dir))
    print(f"phone-lab trace on http://{host}:{port}/trace · runs {runs_dir}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("phone-lab trace stopped", flush=True)
