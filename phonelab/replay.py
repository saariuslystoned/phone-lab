"""Backend protocol, AdbBackend, RunWriter (trace format), and Runner (record + replay)."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import shlex
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Protocol

from PIL import Image

from phonelab.adb import Adb
from phonelab.cua import FIXTURE, CuaDriver, CuaError, MAX_STALE_RETRIES, STALE_REASONS, fixture_state
from phonelab.displays import Display, inventory, to_json
from phonelab.heal import heal
from phonelab.refs import assign_refs, find, label_of, tap_point
from phonelab.sessions import Registry, SessionRecord
from phonelab.trace import RUN_SCHEMA, STEP_SCHEMA, TREE_SCHEMA
from phonelab.trails import (
    TRAIL_SCHEMA,
    Step,
    Trail,
    TrailError,
    default_label,
    derive_predicate,
    load_trail,
    parse_script,
    save_trail,
)

AM_START_TIMEOUT_S = 60  # `am start -W` waits for the launch; a cold debug build can pass adb's 15 s default

RECORDED_NODE_KEYS = (
    "i",
    "class",
    "text",
    "desc",
    "id",
    "bounds",
    "clickable",
    "long_clickable",
    "editable",
    "checkable",
    "focusable",
    "visible",
)


def _iso(t: float | None = None) -> str:
    now = time.time() if t is None else t
    return dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, indent=2) + "\n"
    tmp.write_text(text)
    os.replace(tmp, path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def make_run_id(runs_dir: Path, trail_name: str, kind: str) -> str:
    base = f"{trail_name}-{kind}-{time.strftime('%Y%m%d-%H%M%S')}"
    candidate = base
    counter = 2
    while (runs_dir / candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


class Backend(Protocol):
    adb: Adb
    driver: CuaDriver

    def inventory(self) -> list[Display]: ...
    def screencap(self, sf_id: str) -> bytes | None: ...
    def tree(self, logical_id: int) -> dict: ...
    def act(self, logical_id: int, node_index: int, action: str) -> dict: ...
    def shell(self, *args: str, timeout: float = 15) -> str: ...
    def fixture_state(self) -> dict | None: ...
    def props(self) -> dict: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...


class AdbBackend:
    def __init__(self, adb: Adb, driver_path: Path, treedump_jar: Path, apps: list[str] | tuple[str, ...] | None = None) -> None:
        from phonelab.tree import TreeDumper, packages_with

        self.adb = adb
        self.driver = CuaDriver(adb, driver_path)
        self.apps = list(apps or [])
        pkgs = packages_with(self.apps)
        self.dumper = TreeDumper(adb, treedump_jar, text_packages=pkgs, act_packages=pkgs)

    def add_apps(self, pkgs: Any) -> None:
        from phonelab.tree import packages_with

        merged = packages_with(self.apps + list(pkgs))
        self.apps = list(dict.fromkeys(self.apps + list(pkgs)))
        self.dumper.text_packages = merged
        self.dumper.act_packages = merged

    def inventory(self) -> list[Display]:
        return inventory(self.adb)

    def screencap(self, sf_id: str) -> bytes | None:
        return self.adb.screencap(sf_id)

    def tree(self, logical_id: int) -> dict:
        reply = self.dumper.tree(logical_id)
        if reply.get("ok"):
            assign_refs(reply)
        return reply

    def act(self, logical_id: int, node_index: int, action: str) -> dict:
        return self.dumper.act(logical_id, node_index, action)

    def shell(self, *args: str, timeout: float = 15) -> str:
        return self.adb.shell(*args, timeout=timeout)

    def fixture_state(self) -> dict | None:
        try:
            return fixture_state(self.adb)
        except Exception:
            return None

    def props(self) -> dict:
        p = self.adb.props()
        return {
            "model": self.adb.model,
            "android_release": p.get("android_release"),
            "api_level": p.get("api_level"),
        }

    def start(self) -> None:
        self.dumper.start()

    def stop(self) -> None:
        self.dumper.stop()


def to_tree_doc(reply: dict, logical_id: int, captured_at: float) -> dict:
    read_ms = reply.get("cost_ms", 0)
    windows = reply.get("windows", [])
    package = windows[0].get("package") if windows else reply.get("package")
    window_count = len(windows)

    nodes = []
    for idx, node in enumerate(reply.get("nodes", [])):
        nodes.append({
            "i": node.get("i", idx),
            "parent": node.get("parent"),
            "depth": node.get("depth", 0),
            "ref": node.get("ref"),
            "class": node.get("class"),
            "text": node.get("text"),
            "content_desc": node.get("desc"),
            "resource_id": node.get("id"),
            "bounds": node.get("bounds"),
            "clickable": bool(node.get("clickable", False)),
            "enabled": bool(node.get("enabled", True)),
            "focused": bool(node.get("focused", False)),
        })

    return {
        "schema": TREE_SCHEMA,
        "display_id": logical_id,
        "captured_at": captured_at,
        "read_ms": read_ms,
        "package": package,
        "window_count": window_count,
        "nodes": nodes,
    }


def capture_all(
    backend: Backend,
    displays: list[Display] | None,
    step_dir: Path,
    index: int,
    phase: str,
    *,
    runner_session: SessionRecord | None = None,
    capture_human: bool = True,
    seq_tracker: dict[int, int] | None = None,
) -> tuple[list[dict], int]:
    """Capture every non-ignored display; re-inventories first (a Cua snapshot changes the agent sf_id)."""
    t0 = time.time()
    inv = displays if displays is not None else backend.inventory()
    panels = []

    for d in inv:
        if d.role == "ignored":
            continue

        sf_id = d.sf_id
        unique_id = d.unique_id
        logical_id = d.logical_id
        name = d.name
        role = d.role
        width = d.width
        height = d.height

        if d.state == "OFF":
            panels.append({
                "sf_id": sf_id,
                "unique_id": unique_id,
                "logical_id": logical_id,
                "name": name,
                "role": role,
                "seq": None,
                "captured_at": None,
                "capture_ms": None,
                "width": width,
                "height": height,
                "png_sha256": None,
                "cropped_status_bar_px": 0,
                "image": None,
                "session": None,
            })
            continue

        if not capture_human and role == "human":
            panels.append({
                "sf_id": sf_id,
                "unique_id": unique_id,
                "logical_id": logical_id,
                "name": name,
                "role": role,
                "seq": None,
                "captured_at": None,
                "capture_ms": None,
                "width": width,
                "height": height,
                "png_sha256": None,
                "cropped_status_bar_px": 0,
                "image": None,
                "capture_error": "skipped",
                "session": None,
            })
            continue

        t_cap0 = time.time()
        raw = None
        try:
            raw = backend.screencap(sf_id)
        except Exception:
            raw = None
        t_cap1 = time.time()
        cap_ms = round((t_cap1 - t_cap0) * 1000)

        if raw is None:
            panels.append({
                "sf_id": sf_id,
                "unique_id": unique_id,
                "logical_id": logical_id,
                "name": name,
                "role": role,
                "seq": None,
                "captured_at": None,
                "capture_ms": None,
                "width": width,
                "height": height,
                "png_sha256": None,
                "cropped_status_bar_px": 0,
                "image": None,
                "capture_error": "screencap failed",
                "session": None,
            })
            continue

        try:
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
            cropped_status_bar = 0
            status_bar_px = d.status_bar_px if role == "human" else 0

            if role == "human" and status_bar_px > 0 and h > status_bar_px:
                cropped = img.crop((0, status_bar_px, w, h))
                buf = io.BytesIO()
                cropped.save(buf, format="PNG")
                png_bytes = buf.getvalue()
                final_w, final_h = cropped.size
                cropped_status_bar = status_bar_px
            else:
                png_bytes = raw
                final_w, final_h = w, h

            img_name = f"{phase}-logical-{logical_id}.png"
            img_path = step_dir / img_name
            _atomic_write_bytes(img_path, png_bytes)

            sha = hashlib.sha256(png_bytes).hexdigest()

            seq = None
            if seq_tracker is not None and logical_id is not None:
                seq_tracker[logical_id] = seq_tracker.get(logical_id, 0) + 1
                seq = seq_tracker[logical_id]

            sess_doc = None
            if role == "agent" and runner_session is not None:
                sess_doc = runner_session.to_json()
                sess_doc["lease_remaining_now_ms"] = runner_session.lease_remaining_now()

            panels.append({
                "sf_id": sf_id,
                "unique_id": unique_id,
                "logical_id": logical_id,
                "name": name,
                "role": role,
                "seq": seq,
                "captured_at": t_cap0,
                "capture_ms": cap_ms,
                "width": final_w,
                "height": final_h,
                "png_sha256": sha,
                "cropped_status_bar_px": cropped_status_bar,
                "image": img_name,
                "session": sess_doc,
            })
        except Exception as exc:
            panels.append({
                "sf_id": sf_id,
                "unique_id": unique_id,
                "logical_id": logical_id,
                "name": name,
                "role": role,
                "seq": None,
                "captured_at": None,
                "capture_ms": None,
                "width": width,
                "height": height,
                "png_sha256": None,
                "cropped_status_bar_px": 0,
                "image": None,
                "capture_error": str(exc),
                "session": None,
            })

    total_cap_ms = round((time.time() - t0) * 1000)
    return panels, total_cap_ms


class RunWriter:
    def __init__(
        self,
        runs_dir: Path,
        run_id: str,
        kind: str,
        device: dict,
        trail_path_src: Path | None,
        trail_name: str,
        step_count: int,
        source_run_id: str | None,
        displays: list[Display],
        session: dict | None,
        command: str,
        heal: dict | None = None,
    ) -> None:
        self.runs_dir = Path(runs_dir)
        self.run_id = run_id
        self.kind = kind
        self.device = device
        self.trail_path_src = Path(trail_path_src) if trail_path_src else None
        self.trail_name = trail_name
        self.step_count = step_count
        self.source_run_id = source_run_id
        self.displays = displays
        self.session = session
        self.command = command
        self.heal = heal
        self.run_dir = self.runs_dir / self.run_id
        self.steps_summary: list[dict] = []
        self.started_at = time.time()
        self.created_at = _iso(self.started_at)
        self.finished_at: str | None = None
        self.trail_sha256: str = ""

    def begin(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        trail_dest = self.run_dir / "trail.json"
        if self.trail_path_src and self.trail_path_src.is_file():
            trail_bytes = self.trail_path_src.read_bytes()
            trail_dest.write_bytes(trail_bytes)
        else:
            trail_obj = {"schema": TRAIL_SCHEMA, "name": self.trail_name, "step_count": self.step_count}
            trail_bytes = (json.dumps(trail_obj, indent=2) + "\n").encode()
            trail_dest.write_bytes(trail_bytes)
        self.trail_sha256 = hashlib.sha256(trail_bytes).hexdigest()

        run_doc = {
            "schema": RUN_SCHEMA,
            "run_id": self.run_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "finished_at": None,
            "device": self.device,
            "trail": {
                "name": self.trail_name,
                "path": "trail.json",
                "sha256": self.trail_sha256,
                "step_count": self.step_count,
                "source_run_id": self.source_run_id,
            },
            "displays": [to_json(d) for d in self.displays],
            "session": self.session,
            "heal": self.heal,
            "steps": self.steps_summary,
            "result": None,
            "timings": {
                "started_at": self.started_at,
                "finished_at": None,
                "total_ms": None,
            },
            "tool": {
                "name": "phonelab",
                "version": "0.1",
                "command": self.command,
            },
        }
        _atomic_write_json(self.run_dir / "run.json", run_doc)

    def step_dir(self, index: int) -> Path:
        d = self.run_dir / "steps" / f"{index:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_step(self, step_doc: dict) -> None:
        idx = step_doc["index"]
        s_dir = self.step_dir(idx)
        _atomic_write_json(s_dir / "step.json", step_doc)

        summary = {
            "index": idx,
            "dir": f"steps/{idx:03d}",
            "name": step_doc["name"],
            "kind": step_doc["action"]["kind"],
            "status": step_doc["result"]["status"],
            "duration_ms": step_doc["timings"]["duration_ms"],
            "display_id": step_doc["action"]["display_id"],
        }
        # Update or append
        existing = [i for i, s in enumerate(self.steps_summary) if s["index"] == idx]
        if existing:
            self.steps_summary[existing[0]] = summary
        else:
            self.steps_summary.append(summary)

        # Update run.json
        run_file = self.run_dir / "run.json"
        if run_file.is_file():
            try:
                run_doc = json.loads(run_file.read_text())
                run_doc["steps"] = self.steps_summary
                _atomic_write_json(run_file, run_doc)
            except Exception:
                pass

    def write_tree(self, index: int, phase: str, logical_id: int, tree_doc: dict) -> str:
        filename = f"tree-{phase}-logical-{logical_id}.json"
        path = self.step_dir(index) / filename
        _atomic_write_json(path, tree_doc)
        return filename

    def write_png(self, index: int, phase: str, logical_id: int, png: bytes) -> str:
        filename = f"{phase}-logical-{logical_id}.png"
        path = self.step_dir(index) / filename
        _atomic_write_bytes(path, png)
        return filename

    def finish(self, status: str, message: str | None) -> dict:
        now = time.time()
        self.finished_at = _iso(now)
        total_ms = round((now - self.started_at) * 1000)
        steps_total = self.step_count
        steps_ok = sum(1 for s in self.steps_summary if s["status"] in ("ok", "healed"))
        steps_failed = sum(1 for s in self.steps_summary if s["status"] in ("fail", "refused", "error"))

        trail_file = self.run_dir / "trail.json"
        if trail_file.is_file():
            self.trail_sha256 = hashlib.sha256(trail_file.read_bytes()).hexdigest()

        result_doc = {
            "status": status,
            "steps_total": steps_total,
            "steps_ok": steps_ok,
            "steps_failed": steps_failed,
            "message": message,
        }
        timings_doc = {
            "started_at": self.started_at,
            "finished_at": now,
            "total_ms": total_ms,
        }
        run_doc = {
            "schema": RUN_SCHEMA,
            "run_id": self.run_id,
            "kind": self.kind,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "device": self.device,
            "trail": {
                "name": self.trail_name,
                "path": "trail.json",
                "sha256": self.trail_sha256,
                "step_count": self.step_count,
                "source_run_id": self.source_run_id,
            },
            "displays": [to_json(d) for d in self.displays],
            "session": self.session,
            "heal": self.heal,
            "steps": self.steps_summary,
            "result": result_doc,
            "timings": timings_doc,
            "tool": {
                "name": "phonelab",
                "version": "0.1",
                "command": self.command,
            },
        }
        _atomic_write_json(self.run_dir / "run.json", run_doc)
        return run_doc


class Runner:
    def __init__(
        self,
        backend: Backend,
        registry: Registry,
        runs_dir: Path,
        *,
        capture_human: bool = True,
        poll_ms: int = 400,
        log: Callable[[str], None] = print,
        max_heal_px: int = 120,
        cua_size: str | None = None,
        cua_density: int | None = None,
    ) -> None:
        self.backend = backend
        self.registry = registry
        self.runs_dir = Path(runs_dir)
        self.capture_human = capture_human
        self.poll_ms = poll_ms
        self.log = log
        self.max_heal_px = max_heal_px
        self.cua_size = cua_size
        self.cua_density = cua_density
        self.session_record: SessionRecord | None = None
        self.display_id: int | None = None
        self.target_id: str | None = None
        self.resized_displays: set[int] = set()
        self.seq_tracker: dict[int, int] = defaultdict(int)

    def open_session(self, allow_apps: list[str], label: str) -> SessionRecord:
        created = self.backend.driver.create(allow_apps, label, size=self.cua_size, density=self.cua_density)
        now = time.time()
        sid = created["data"]["session_id"]
        disp_id = created["data"].get("display_id")
        lease_ms = int(created["data"].get("lease_remaining_ms", 0))

        tag = getattr(self.backend.adb, "tag", None)
        rec = SessionRecord(
            session_id=sid,
            label=created["data"].get("label") or label,
            display_id=disp_id,
            package=None,
            target_id=None,
            state="active",
            lease_remaining_ms=lease_ms,
            lease_checked_at=now,
            last_action={"kind": "create", "at": now, "result": "ok", "detail": {}},
            owner="phonelab trail",
            updated_at=now,
            device_tag=tag,
        )
        self.session_record = rec
        self.display_id = disp_id
        self.registry.write(rec)
        return rec

    def _fixture_oracle(self) -> bool:
        """The counter oracle belongs to the Cua fixture; steps in other apps get no derived launch/tap predicate."""
        return self.session_record is not None and self.session_record.package == FIXTURE

    def close_session(self) -> None:
        for d in sorted(self.resized_displays):
            try:
                self.backend.shell("wm", "size", "reset", "-d", str(d))
            except Exception:
                pass
            try:
                self.backend.shell("wm", "density", "reset", "-d", str(d))
            except Exception:
                pass
        self.resized_displays.clear()

        if self.session_record and self.session_record.state == "active":
            try:
                self.backend.driver.stop(self.session_record.session_id)
            except Exception:
                pass
            now = time.time()
            self.session_record.state = "stopped"
            self.session_record.updated_at = now
            self.registry.write(self.session_record)
        self.display_id = None
        self.target_id = None

    def resolve_display(self, alias: str) -> int:
        if alias == "agent":
            if self.display_id is None:
                raise TrailError("agent display not known (no active session)")
            return self.display_id
        try:
            return int(alias)
        except ValueError:
            raise TrailError(f"unsupported display alias: {alias!r}")

    def check_predicate(self, pred: dict, logical_id: int) -> tuple[bool, dict]:
        kind = pred.get("kind")
        if kind == "fixture_counter":
            expected = pred.get("expected")
            st = self.backend.fixture_state()
            if st is None:
                return False, {"error": "no oracle response"}
            cnt = st.get("counter")
            return (cnt == expected), {"counter": cnt, "expected": expected}
        if kind == "text_present":
            target = pred.get("text")
            tree_reply = self.backend.tree(logical_id)
            for node in tree_reply.get("nodes", []):
                if node.get("text") == target or node.get("desc") == target:
                    return True, {"observed_text": target}
            return False, {"observed_text": None}
        if kind == "ref_present":
            target_ref = pred.get("ref")
            tree_reply = self.backend.tree(logical_id)
            node = find(tree_reply, target_ref)
            return (node is not None), {"ref_found": node is not None}
        if kind == "ref_absent":
            target_ref = pred.get("ref")
            tree_reply = self.backend.tree(logical_id)
            node = find(tree_reply, target_ref)
            return (node is None), {"ref_found": node is not None}
        return False, {"error": f"unknown predicate kind {kind}"}

    def wait_predicate(self, pred: dict, logical_id: int) -> tuple[bool, int, dict]:
        timeout_ms = pred.get("timeout_ms", 5000)
        t0 = time.monotonic()
        last_detail: dict[str, Any] = {}

        while True:
            held, detail = self.check_predicate(pred, logical_id)
            last_detail.update(detail)
            if held:
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                last_detail["predicate_ms"] = elapsed_ms
                return True, elapsed_ms, last_detail
            now = time.monotonic()
            if (now - t0) * 1000 >= timeout_ms:
                elapsed_ms = round((now - t0) * 1000)
                last_detail["predicate_ms"] = elapsed_ms
                return False, elapsed_ms, last_detail
            time.sleep(min(self.poll_ms / 1000.0, max(0.01, (timeout_ms - (now - t0) * 1000) / 1000.0)))

    def run_step(
        self,
        index: int,
        step: Step,
        writer: RunWriter,
        *,
        derive: bool = False,
        recording: bool | None = None,
        total_steps: int = 1,
    ) -> tuple[dict, dict | None]:
        # `derive` is about the predicate; `recording` is about resolving labels and storing the node.
        if recording is None:
            recording = derive
        started_at = time.time()
        step_dir = writer.step_dir(index)

        # 1. Lease check
        if self.session_record and self.session_record.lease_remaining_now() < 30000:
            try:
                renew_reply = self.backend.driver.renew(self.session_record.session_id)
                now = time.time()
                self.session_record.lease_remaining_ms = int(
                    renew_reply.get("data", {}).get("lease_remaining_ms", 60000)
                )
                self.session_record.lease_checked_at = now
                self.session_record.updated_at = now
                self.registry.write(self.session_record)
            except Exception:
                pass

        # 2. Before capture
        captures_before, cap_b_ms = capture_all(
            self.backend,
            None,
            step_dir,
            index,
            "before",
            runner_session=self.session_record,
            capture_human=self.capture_human,
            seq_tracker=self.seq_tracker,
        )

        # 3. Before tree
        display_id = self.resolve_display(step.display)
        t_tree_b0 = time.time()
        tree_before = self.backend.tree(display_id)
        tree_before_doc = to_tree_doc(tree_before, display_id, t_tree_b0)
        tree_b_file = writer.write_tree(index, "before", display_id, tree_before_doc)
        phase_tree_before_ms = round((time.time() - t_tree_b0) * 1000)
        trees_before = {f"logical-{display_id}": tree_b_file}

        # 4. Ref resolution
        kind = step.action.get("kind", "")
        status = "ok"
        message = None
        action_detail: dict[str, Any] = {}
        result_detail: dict[str, Any] = {}
        frame_stale_retries = 0
        tap_x = tap_y = None
        node_index = None
        trees_heal: dict[str, str] | None = None

        if kind in ("tap", "set_text"):
            label = step.action.get("label")
            ref = step.action.get("ref", "")
            if not ref and label:
                if recording:
                    if not tree_before.get("ok"):
                        status = "fail"
                        message = f"label {label!r} cannot be resolved: tree read failed ({tree_before.get('error') or 'no refs'})"
                    else:
                        from phonelab.refs import resolve_label
                        candidates = resolve_label(tree_before, label, action_kind=kind)
                        if len(candidates) != 1:
                            status = "fail"
                            message = f"label {label!r} matched {len(candidates)} nodes (expected 1)"
                        else:
                            node = candidates[0]
                            ref = node.get("ref", "")
                            step.action["ref"] = ref
                else:
                    status = "fail"
                    message = f"ref missing for label {label!r}; trail was never recorded, re-record to resolve refs"

            if status in ("ok", "healed"):
                node = find(tree_before, ref)
                if node is None and label and not recording and tree_before.get("ok"):
                    # A step written by label keeps the author's intent: re-resolve it before the geometric
                    # heal, which needs the node's own label (a Compose EditText's label lives in a child).
                    from phonelab.refs import resolve_label
                    candidates = resolve_label(tree_before, label, action_kind=kind)
                    if len(candidates) == 1:
                        node = candidates[0]
                        status = "healed"
                        message = f"ref {ref} re-resolved by label {label!r} to {node.get('ref')}"
                        action_detail["ref_used"] = node.get("ref")
                        result_detail["heal"] = {
                            "status": "healed",
                            "ref": node.get("ref"),
                            "note": {
                                "kind": "healed",
                                "reason": "label",
                                "missing_ref": ref,
                                "ref": node.get("ref"),
                                "label": label,
                            },
                        }
                if node is None:
                    if not tree_before.get("ok") or "refs" not in tree_before:
                        status = "fail"
                        message = f"ref {ref} not found: tree read failed ({tree_before.get('error') or 'no refs'}); heal skipped"
                    else:
                        recorded_spec = step.action.get("recorded")
                        ref_count = len([n for n in tree_before.get("nodes", []) if n.get("ref")])
                        if not recorded_spec:
                            status = "fail"
                            message = f"ref {ref} not found in tree ({ref_count} refs); trail has no recorded node, re-record to enable healing"
                        else:
                            recorded_node = dict(recorded_spec)
                            recorded_node["ref"] = ref
                            recorded_tree = {"nodes": [recorded_node], "refs": {"count": 1, "refs": {ref: 0}}}
                            res = heal(ref, recorded_tree, tree_before, max_distance_px=self.max_heal_px)
                            recorded_file = writer.write_tree(index, "recorded", display_id, recorded_tree)
                            current_file = writer.write_tree(index, "current", display_id, tree_before)
                            trees_heal = {"recorded": recorded_file, "current": current_file}
                            result_detail["heal"] = res.to_json()
                            note = res.note
                            if res.status == "healed":
                                node = res.node
                                status = "healed"
                                message = f"ref {ref} healed: {note['reason']} {note['distance_px']} px"
                                action_detail["ref_used"] = res.ref
                                if kind == "tap":
                                    tap_x, tap_y = tap_point(node)
                                elif kind == "set_text":
                                    node_index = node.get("i", 0)
                            else:
                                status = "fail"
                                message = f"ref {ref} missing: {note['reason']} ({note['message']}); trees {recorded_file} and {current_file}"
                else:
                    if recording:
                        step.action["recorded"] = {k: node[k] for k in RECORDED_NODE_KEYS if k in node}
                    if kind == "tap":
                        tap_x, tap_y = tap_point(node)
                        hint = {
                            "class": node.get("class"),
                            "label": label_of(node),
                            "resource_id": node.get("id"),
                        }
                        if recording:
                            step.action["hint"] = hint
                            id_part = (node.get("id") or "").split("/")[-1]
                            if id_part and (step.name == f"tap {ref}" or step.name == "tap increment"):
                                step.name = f"tap {id_part}"
                    elif kind == "set_text":
                        node_index = node.get("i", 0)

        # 4b. Record mode: read the oracle before a tap so the derived predicate waits for a change
        counter_before = None
        if derive and kind == "tap" and status in ("ok", "healed") and step.predicate is None and self._fixture_oracle():
            st0 = self.backend.fixture_state()
            counter_before = st0.get("counter") if st0 else None

        # 5. Action
        t_act0 = time.time()
        if status in ("ok", "healed"):
            try:
                if kind == "launch":
                    pkg = step.action["package"]
                    fresh = step.action.get("fresh", True)
                    activity = step.action.get("activity")
                    extras = step.action.get("extras")
                    if fresh:
                        self.backend.shell("am", "force-stop", pkg)
                    if extras:
                        am_cmd = ["am", "start", "-W", "--display", str(display_id), "-n", f"{pkg}/{activity}"]
                        for k, v in extras.items():
                            if isinstance(v, bool):
                                am_cmd.extend(["--ez", k, "true" if v else "false"])
                            elif isinstance(v, int):
                                am_cmd.extend(["--ei", k, str(v)])
                            elif isinstance(v, str):
                                am_cmd.extend(["--es", k, v])
                        # adb joins the argv with spaces for the device shell: quote every argument.
                        am_out = self.backend.shell(*[shlex.quote(a) for a in am_cmd], timeout=AM_START_TIMEOUT_S)
                        am_lines = [ln.strip() for ln in (am_out or "").splitlines()]
                        am_errors = [ln for ln in am_lines if ln.startswith("Error")]
                        am_status = [ln for ln in am_lines if ln.startswith("Status:")]
                        if am_errors:
                            raise RuntimeError(f"am start failed: {am_errors[-1]}")
                        if am_status and am_status[-1].split(":", 1)[1].strip() != "ok":
                            raise RuntimeError(f"am start failed: {am_status[-1]}")
                        self.target_id = None
                        if self.session_record:
                            self.session_record.package = pkg
                            self.session_record.target_id = None
                        action_detail.update({"package": pkg, "via": "am", "extras": dict(extras)})
                        if activity:
                            action_detail["activity"] = activity
                    else:
                        launch_kwargs = {}
                        if activity:
                            launch_kwargs["activity"] = activity
                        launch_resp = self.backend.driver.launch(self.session_record.session_id, pkg, **launch_kwargs)
                        self.target_id = launch_resp.get("data", {}).get("target_id")
                        if self.session_record:
                            self.session_record.package = pkg
                            self.session_record.target_id = self.target_id
                        action_detail.update({"package": pkg, "via": "cua"})
                        if activity:
                            action_detail["activity"] = activity
                elif kind == "tap":
                    if self.target_id is None:
                        self.backend.shell("input", "-d", str(display_id), "tap", str(tap_x), str(tap_y))
                        action_detail.update({
                            "via": "input",
                            "x": tap_x,
                            "y": tap_y,
                            "package": self.session_record.package if self.session_record else None,
                        })
                    else:
                        while True:
                            snap = self.backend.driver.snapshot(self.session_record.session_id, self.target_id)
                            snap_id = snap["data"]["snapshot_id"]
                            try:
                                self.backend.driver.tap(self.session_record.session_id, snap_id, tap_x, tap_y)
                                break
                            except CuaError as exc:
                                if exc.reason in STALE_REASONS and frame_stale_retries < MAX_STALE_RETRIES:
                                    frame_stale_retries += 1
                                    continue
                                raise
                        action_detail.update({
                            "via": "cua",
                            "x": tap_x,
                            "y": tap_y,
                            "snapshot_id": snap_id,
                            "package": self.session_record.package if self.session_record else None,
                        })
                elif kind == "set_text":
                    focus = self.backend.act(display_id, node_index, "focus")
                    if not focus.get("ok") or focus.get("performed") is False:
                        raise RuntimeError(f"focus failed: {focus.get('error') or 'not performed'}; text not typed")
                    if step.action.get("clear_first", True):
                        self.backend.shell("input", "-d", str(display_id), "keycombination", "KEYCODE_CTRL_LEFT", "KEYCODE_A")
                        self.backend.shell("input", "-d", str(display_id), "keyevent", "KEYCODE_DEL")
                    txt = step.action["text"]
                    escaped_txt = txt.replace(" ", "%s")
                    self.backend.shell("input", "-d", str(display_id), "text", escaped_txt)
                    action_detail.update({"text": txt, "clear_first": step.action.get("clear_first", True)})
                elif kind == "key":
                    keycode = step.action["keycode"]
                    self.backend.shell("input", "-d", str(display_id), "keyevent", keycode)
                    action_detail.update({"keycode": keycode})
                elif kind == "swipe":
                    frm = step.action["from"]
                    to = step.action["to"]
                    dur = step.action.get("duration_ms", 300)
                    self.backend.shell(
                        "input", "-d", str(display_id), "swipe",
                        str(frm[0]), str(frm[1]), str(to[0]), str(to[1]), str(dur),
                    )
                    action_detail.update({"from": frm, "to": to, "duration_ms": dur})
                elif kind == "sleep":
                    ms = step.action["ms"]
                    time.sleep(ms / 1000.0)
                    action_detail.update({"ms": ms})
                elif kind == "wait_for":
                    pass
                elif kind == "resize":
                    size = step.action.get("size")
                    density = step.action.get("density")
                    self.resized_displays.add(display_id)
                    if size == "reset":
                        self.backend.shell("wm", "size", "reset", "-d", str(display_id))
                        self.backend.shell("wm", "density", "reset", "-d", str(display_id))
                    else:
                        self.backend.shell("wm", "size", size, "-d", str(display_id))
                        if density is not None:
                            self.backend.shell("wm", "density", str(density), "-d", str(display_id))
                    action_detail.update({"size": size, "density": density, "display_id": display_id})
            except CuaError as exc:
                status = "refused" if exc.status == "refused" else "error"
                message = exc.reason
            except Exception as exc:
                status = "error"
                message = str(exc)
        if kind in ("tap", "set_text") and step.action.get("label") and action_detail:
            action_detail["label"] = step.action["label"]
        phase_action_ms = round((time.time() - t_act0) * 1000)

        if kind == "tap":
            result_detail["frame_stale_retries"] = frame_stale_retries

        # 6. Wait / Predicate
        t_wait0 = time.time()
        used_predicate = step.predicate
        if status in ("ok", "healed"):
            if derive and step.predicate is None:
                if kind in ("launch", "tap") and not self._fixture_oracle():
                    pkg = self.session_record.package if self.session_record else None
                    message = f"no oracle for {pkg}; predicate omitted"
                elif kind == "launch":
                    pkg = step.action.get("package")
                    t_poll0 = time.monotonic()
                    timeout_ms = 8000
                    st = None
                    while (time.monotonic() - t_poll0) * 1000 < timeout_ms:
                        st = self.backend.fixture_state()
                        if st is not None:
                            break
                        time.sleep(self.poll_ms / 1000.0)
                    if st is not None:
                        used_predicate = {"kind": "fixture_counter", "expected": st["counter"], "timeout_ms": timeout_ms}
                        result_detail["counter_before"] = None
                        result_detail["counter_after"] = st["counter"]
                        result_detail["predicate_ms"] = round((time.monotonic() - t_poll0) * 1000)
                    else:
                        message = "no oracle; predicate omitted"
                elif kind == "tap":
                    t_poll0 = time.monotonic()
                    timeout_ms = 5000
                    st = None
                    while True:
                        st = self.backend.fixture_state()
                        if st is not None and (counter_before is None or st.get("counter") != counter_before):
                            break
                        if (time.monotonic() - t_poll0) * 1000 >= timeout_ms:
                            break
                        time.sleep(self.poll_ms / 1000.0)
                    if st is not None and (counter_before is None or st.get("counter") != counter_before):
                        used_predicate = {"kind": "fixture_counter", "expected": st["counter"], "timeout_ms": timeout_ms}
                        result_detail["counter_before"] = counter_before
                        result_detail["counter_after"] = st["counter"]
                        result_detail["predicate_ms"] = round((time.monotonic() - t_poll0) * 1000)
                    elif st is None:
                        message = "no oracle; predicate omitted"
                    else:
                        status = "fail"
                        message = f"counter stayed at {counter_before} for {timeout_ms} ms after the tap"
                else:
                    used_predicate = derive_predicate(
                        kind,
                        step.action,
                        {"package": self.session_record.package if self.session_record else None},
                    )
            elif step.predicate is not None:
                held, pred_ms, pred_detail = self.wait_predicate(step.predicate, display_id)
                result_detail.update(pred_detail)
                if not held:
                    status = "fail"
                    message = f"predicate {step.predicate.get('kind')} timed out after {step.predicate.get('timeout_ms')}ms"
        phase_wait_ms = round((time.time() - t_wait0) * 1000)

        # 7. After capture & after tree
        captures_after, cap_a_ms = capture_all(
            self.backend,
            None,
            step_dir,
            index,
            "after",
            runner_session=self.session_record,
            capture_human=self.capture_human,
            seq_tracker=self.seq_tracker,
        )

        t_tree_a0 = time.time()
        tree_after = self.backend.tree(display_id)
        tree_after_doc = to_tree_doc(tree_after, display_id, t_tree_a0)
        tree_a_file = writer.write_tree(index, "after", display_id, tree_after_doc)
        phase_tree_after_ms = round((time.time() - t_tree_a0) * 1000)
        trees_after = {f"logical-{display_id}": tree_a_file}

        # 8. Assemble step.json
        finished_at = time.time()
        phases = {
            "capture_before_ms": cap_b_ms,
            "tree_before_ms": phase_tree_before_ms,
            "action_ms": phase_action_ms,
            "wait_ms": phase_wait_ms,
            "capture_after_ms": cap_a_ms,
            "tree_after_ms": phase_tree_after_ms,
        }
        duration_ms = sum(phases.values())

        pred_doc = None
        if used_predicate is not None:
            pred_doc = dict(used_predicate)
            pred_doc["display_id"] = display_id

        action_doc = {
            "kind": kind,
            "display_id": display_id,
            "ref": step.action.get("ref"),
            "detail": action_detail,
        }
        result_doc = {
            "status": status,
            "message": message,
            "detail": result_detail,
        }
        timings_doc = {
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "phases": phases,
        }
        trees_doc: dict[str, Any] = {"before": trees_before, "after": trees_after}
        if trees_heal is not None:
            trees_doc["heal"] = trees_heal

        step_doc = {
            "schema": STEP_SCHEMA,
            "index": index,
            "name": step.name,
            "action": action_doc,
            "predicate": pred_doc,
            "result": result_doc,
            "timings": timings_doc,
            "captures": {"before": captures_before, "after": captures_after},
            "trees": trees_doc,
        }
        writer.write_step(step_doc)

        if self.session_record:
            self.session_record.last_action = {
                "kind": kind,
                "at": finished_at,
                "result": status,
                "detail": {
                    "ref": step.action.get("ref"),
                    "x": action_detail.get("x"),
                    "y": action_detail.get("y"),
                    "step": index,
                },
            }
            self.session_record.updated_at = finished_at
            self.registry.write(self.session_record)

        log_line = (
            f"step {index + 1}/{total_steps} {step.name} -> {status} {duration_ms} ms "
            f"(action {phase_action_ms}, wait {phase_wait_ms})"
        )
        self.log(self.backend.adb.redact(log_line))

        return step_doc, used_predicate


def _make_skipped_step(index: int, step: Step, display_id: int | None) -> dict:
    now = time.time()
    pred_doc = None
    if step.predicate:
        pred_doc = dict(step.predicate)
        pred_doc["display_id"] = display_id

    return {
        "schema": STEP_SCHEMA,
        "index": index,
        "name": step.name,
        "action": {
            "kind": step.action.get("kind"),
            "display_id": display_id,
            "ref": step.action.get("ref"),
            "detail": {},
        },
        "predicate": pred_doc,
        "result": {
            "status": "skipped",
            "message": "skipped after earlier failure",
            "detail": {},
        },
        "timings": {
            "started_at": now,
            "finished_at": now,
            "duration_ms": 0,
            "phases": {},
        },
        "captures": {"before": [], "after": []},
        "trees": {"before": {}, "after": {}},
    }


def record(
    backend: Backend,
    registry: Registry,
    runs_dir: Path,
    trails_dir: Path,
    name: str,
    script_text: str,
    *,
    capture_human: bool = True,
    cua_size: str | None = None,
    cua_density: int | None = None,
    apps: list[str] | tuple[str, ...] | None = None,
) -> int:
    runs_dir = Path(runs_dir)
    trails_dir = Path(trails_dir)
    parsed_steps = parse_script(script_text)

    # Determine packages
    launched_pkgs: list[str] = []
    for s in parsed_steps:
        if s["action"].get("kind") == "launch" and s["action"].get("package"):
            pkg = s["action"]["package"]
            if pkg not in launched_pkgs:
                launched_pkgs.append(pkg)
    allow_apps = launched_pkgs if launched_pkgs else ["ai.cua.fixture.notes"]
    if hasattr(backend, "add_apps"):
        backend.add_apps(list(apps or []) + list(allow_apps))

    runner = Runner(backend, registry, runs_dir, capture_human=capture_human,
                    cua_size=cua_size, cua_density=cua_density)
    writer: RunWriter | None = None
    try:
        backend.start()
        session_rec = runner.open_session(allow_apps, default_label(name))

        run_id = make_run_id(runs_dir, name, "record")
        device_info = backend.props()
        writer = RunWriter(
            runs_dir=runs_dir,
            run_id=run_id,
            kind="record",
            device=device_info,
            trail_path_src=None,
            trail_name=name,
            step_count=len(parsed_steps),
            source_run_id=None,
            displays=backend.inventory(),
            session=session_rec.to_json(),
            command="record",
            heal=None,
        )
        writer.begin()

        recorded_steps: list[Step] = []
        run_status = "pass"
        fail_message = None

        for idx, item in enumerate(parsed_steps):
            step = Step(
                name=item["name"],
                display="agent",
                action=item["action"],
                predicate=item.get("predicate"),
            )
            step_doc, used_pred = runner.run_step(
                idx,
                step,
                writer,
                derive=not item.get("explicit_predicate"),
                recording=True,
                total_steps=len(parsed_steps),
            )
            step.predicate = used_pred
            recorded_steps.append(step)

            if step_doc["result"]["status"] not in ("ok", "healed"):
                run_status = "fail"
                fail_message = step_doc["result"].get("message")
                break

        now = time.time()
        trail = Trail(
            name=name,
            created_at=_iso(now),
            recorded_on=device_info,
            session={"allow_apps": allow_apps, "label": default_label(name)},
            steps=recorded_steps,
        )
        save_trail(trail, writer.run_dir / "trail.json")

        if run_status == "pass":
            save_trail(trail, trails_dir / f"{name}.json")

        writer.finish(run_status, fail_message)
        return 0 if run_status == "pass" else 1
    except KeyboardInterrupt:
        if writer is not None and writer.finished_at is None:
            writer.finish("aborted", "interrupted")
        raise
    except Exception as exc:
        if writer is not None and writer.finished_at is None:
            writer.finish("aborted", backend.adb.redact(str(exc)))
        raise
    finally:
        runner.close_session()
        backend.stop()


def replay(
    backend: Backend,
    registry: Registry,
    runs_dir: Path,
    trail_path: Path,
    *,
    times: int = 1,
    source_run_id: str | None = None,
    capture_human: bool = True,
    stop_on_fail: bool = True,
    max_heal_px: int = 120,
    cua_size: str | None = None,
    cua_density: int | None = None,
    apps: list[str] | tuple[str, ...] | None = None,
) -> int:
    runs_dir = Path(runs_dir)
    trail_path = Path(trail_path)
    trail = load_trail(trail_path)

    runner = Runner(
        backend,
        registry,
        runs_dir,
        capture_human=capture_human,
        max_heal_px=max_heal_px,
        cua_size=cua_size,
        cua_density=cua_density,
    )
    allow_apps = trail.session.get("allow_apps", ["ai.cua.fixture.notes"])
    if hasattr(backend, "add_apps"):
        backend.add_apps(list(apps or []) + list(allow_apps))
    label = trail.session.get("label", default_label(trail.name))

    results: list[dict] = []
    overall_ok = True
    writer: RunWriter | None = None

    try:
        backend.start()
        for iter_idx in range(times):
            writer = None
            # One Cua session per run: after `am force-stop` a second `app launch` in the same
            # session is refused with `owned_task_missing` (measured on the Fold, 2026-09-27).
            session_rec = runner.open_session(allow_apps, label)
            run_id = make_run_id(runs_dir, trail.name, "replay")
            device_info = backend.props()
            writer = RunWriter(
                runs_dir=runs_dir,
                run_id=run_id,
                kind="replay",
                device=device_info,
                trail_path_src=trail_path,
                trail_name=trail.name,
                step_count=len(trail.steps),
                source_run_id=source_run_id,
                displays=backend.inventory(),
                session=session_rec.to_json(),
                command="replay",
                heal={"max_distance_px": max_heal_px},
            )
            writer.begin()

            t_iter0 = time.time()
            iter_status = "pass"
            iter_message = None
            has_failed = False

            display_id = runner.display_id
            for idx, step in enumerate(trail.steps):
                if has_failed and stop_on_fail:
                    skipped_doc = _make_skipped_step(idx, step, display_id)
                    writer.write_step(skipped_doc)
                    continue

                step_doc, _ = runner.run_step(
                    idx,
                    step,
                    writer,
                    derive=False,
                    total_steps=len(trail.steps),
                )
                if step_doc["result"]["status"] not in ("ok", "healed"):
                    has_failed = True
                    iter_status = "fail"
                    iter_message = step_doc["result"].get("message")

            iter_duration = time.time() - t_iter0
            writer.finish(iter_status, iter_message)
            runner.close_session()
            results.append({"status": iter_status, "duration_s": iter_duration})
            if iter_status != "pass":
                overall_ok = False
    except KeyboardInterrupt:
        if writer is not None and writer.finished_at is None:
            writer.finish("aborted", "interrupted")
        raise
    except Exception as exc:
        if writer is not None and writer.finished_at is None:
            writer.finish("aborted", backend.adb.redact(str(exc)))
        raise
    finally:
        runner.close_session()
        backend.stop()

    pass_count = sum(1 for r in results if r["status"] == "pass")
    dur_str = ", ".join(f"{r['duration_s']:.1f} s" for r in results)
    runner.log(f"replay {trail.name}: {pass_count}/{times} pass · {dur_str}")
    return 0 if overall_ok else 1
