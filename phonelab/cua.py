"""cua-driver wrapper (host CLI, Android backend) and the synthetic-fixture demo loop."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .adb import Adb, AdbError
from .sessions import Registry, SessionRecord

FIXTURE = "ai.cua.fixture.notes"
DEMO_LABEL = "phone-lab demo"
DEMO_OWNER = "phonelab cua demo"
RENEW_EVERY_S = 10.0
INSPECT_EVERY_S = 2.0
STALE_REASONS = {"frame_stale", "stale_snapshot"}
MAX_STALE_RETRIES = 3


class CuaError(Exception):
    """cua-driver refused or failed a call. The message is redacted; `reason` is the driver's reason code."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(f"cua-driver {status}: {reason}")
        self.status = status
        self.reason = reason


class CuaDriver:
    """Runs `<binary> --device <serial> [--session <sid>] <args...>` and parses the JSON reply."""

    def __init__(self, adb: Adb, binary: Path) -> None:
        self.adb = adb
        self.binary = Path(binary)

    def call(self, *args: str, session: str | None = None, timeout: float = 30) -> dict:
        argv = [str(self.binary), "--device", self.adb.serial or ""]
        if session:
            argv += ["--session", session]
        argv += [str(a) for a in args]
        verb = " ".join(str(a) for a in args[:2])
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except OSError as exc:
            raise CuaError("error", self.adb.redact(f"could not start cua-driver: {exc}")) from None
        except subprocess.TimeoutExpired:
            raise CuaError("error", f"{verb} timed out after {timeout}s") from None
        try:
            reply = json.loads(proc.stdout)
        except ValueError:
            detail = (proc.stdout[:200] + " " + proc.stderr[:200]).strip()
            raise CuaError("error", self.adb.redact(f"{verb}: non-JSON reply (exit {proc.returncode}): {detail}")) from None
        data = reply.get("data")
        if isinstance(data, dict):
            data.pop("image_base64", None)
        exit_code = reply.get("exit_code", proc.returncode)
        if exit_code != 0 or reply.get("status") != "ok":
            error = reply.get("error") or {}
            reason = error.get("reason") or error.get("message") or "unknown"
            raise CuaError(str(reply.get("status") or "error"), self.adb.redact(str(reason)))
        return reply

    def create(self, allow_apps: list[str], label: str, *, size: str | None = None, density: int | None = None) -> dict:
        """`size` is WIDTHxHEIGHT and `density` dpi for the Cua virtual display (driver defaults: 1080x1920, 320)."""
        args: list[str] = ["session", "create"]
        for app in allow_apps:
            args += ["--allow-app", app]
        if size:
            args += ["--size", size]
        if density:
            args += ["--density", str(density)]
        return self.call(*args, "--label", label)

    def launch(self, sid: str, package: str) -> dict:
        return self.call("app", "launch", "--package", package, session=sid)

    def inspect(self, sid: str) -> dict:
        return self.call("session", "inspect", session=sid)

    def renew(self, sid: str) -> dict:
        return self.call("session", "renew", session=sid)

    def snapshot(self, sid: str, target: str) -> dict:
        return self.call("snapshot", "--target", target, session=sid)

    def tap(self, sid: str, snapshot_id: str, x: int, y: int) -> dict:
        return self.call("tap", "--snapshot", snapshot_id, "--x", str(x), "--y", str(y), session=sid)

    def stop(self, sid: str) -> dict:
        return self.call("session", "stop", session=sid)


def parse_fixture_state(text: str) -> dict:
    """The JSON after `json=` in one `content query` row."""
    index = text.find("json=")
    if index < 0:
        raise ValueError("fixture state row has no json= column")
    value, _ = json.JSONDecoder().raw_decode(text[index + len("json="):].lstrip())
    return value


def fixture_state(adb: Adb, package: str = FIXTURE) -> dict:
    """Read the synthetic fixture's state oracle (about 1.3 s over USB)."""
    return parse_fixture_state(adb.shell("content", "query", "--uri", f"content://{package}.state"))


def _log(adb: Adb, message: str) -> None:
    print(adb.redact(f"{time.strftime('%H:%M:%S')} {message}"), flush=True)


def _refresh(rec: SessionRecord, data: dict) -> None:
    """Copy the fresh lease and identity fields from a create/inspect/renew reply into the record."""
    now = time.time()
    if "lease_remaining_ms" in data:
        rec.lease_remaining_ms = int(data["lease_remaining_ms"])
        rec.lease_checked_at = now
    rec.display_id = data.get("display_id", rec.display_id)
    rec.package = data.get("package") or rec.package
    rec.target_id = data.get("target_id") or rec.target_id
    rec.label = data.get("label") or rec.label
    if data.get("state") not in (None, "active"):
        rec.state = "lost"
    rec.updated_at = now


def _tap_increment(adb: Adb, driver: CuaDriver, sid: str, target: str) -> dict:
    """Read the oracle, snapshot, tap the increment button; retry stale frames up to three times."""
    at = time.time()
    detail: dict = {"counter_before": None, "frame_age_ms": None, "frame_stale_retries": 0}
    try:
        state = fixture_state(adb)
        point = state["controls"]["increment"]
        detail["counter_before"] = state.get("counter")
        detail["x"], detail["y"] = point["x"], point["y"]
    except (AdbError, KeyError, TypeError, ValueError) as exc:
        result = f"error:fixture_state {adb.redact(str(exc))[:80]}"
        _log(adb, f"tap increment skipped: {result}")
        return {"kind": "tap increment", "at": at, "result": result, "detail": detail}
    while True:
        try:
            snap = driver.snapshot(sid, target)["data"]
            detail["frame_age_ms"] = snap.get("frame_age_ms")
            driver.tap(sid, snap["snapshot_id"], point["x"], point["y"])
            result = "ok"
            break
        except CuaError as exc:
            if exc.reason in STALE_REASONS and detail["frame_stale_retries"] < MAX_STALE_RETRIES:
                detail["frame_stale_retries"] += 1
                _log(adb, f"tap refused ({exc.reason}); fresh snapshot, retry {detail['frame_stale_retries']}/{MAX_STALE_RETRIES}")
                continue
            result = f"refused:{exc.reason}" if exc.status == "refused" else f"error:{exc.reason}"
            break
    _log(adb, f"tap increment ({point['x']},{point['y']}) counter_before={detail['counter_before']} "
              f"frame_age_ms={detail['frame_age_ms']} retries={detail['frame_stale_retries']} -> {result}")
    return {"kind": "tap increment", "at": at, "result": result, "detail": detail}


def demo(adb: Adb, driver: CuaDriver, registry: Registry, duration_s: int, tap_every_s: float, taps: bool) -> int:
    """Create a fixture session, keep it alive, tap the counter, and keep the registry current. Returns 0 on a clean exit."""
    created = driver.create([FIXTURE], DEMO_LABEL)["data"]
    sid = created["session_id"]
    now = time.time()
    rec = SessionRecord(session_id=sid, label=created.get("label") or DEMO_LABEL, display_id=created.get("display_id"),
                        package=None, target_id=None, state="active",
                        lease_remaining_ms=int(created.get("lease_remaining_ms", 0)), lease_checked_at=now,
                        last_action={"kind": "create", "at": now, "result": "ok", "detail": {}},
                        owner=DEMO_OWNER, updated_at=now, device_tag=getattr(adb, "tag", None))
    registry.write(rec)
    _log(adb, f"session {sid[:8]} created · logical display {rec.display_id} · lease {rec.lease_remaining_ms} ms")
    rc = 0
    try:
        launched = driver.launch(sid, FIXTURE)["data"]
        rec.package = launched.get("package") or FIXTURE
        rec.target_id = launched.get("target_id")
        rec.last_action = {"kind": "launch", "at": time.time(), "result": "ok",
                           "detail": {"activity": launched.get("activity"), "target_id": rec.target_id}}
        _refresh(rec, driver.inspect(sid)["data"])
        registry.write(rec)
        _log(adb, f"launched {rec.package} · target {str(rec.target_id)[:8]} · display {rec.display_id}")
        started = time.monotonic()
        last_renew = started
        last_tap = started - tap_every_s  # first tap right away
        while time.monotonic() - started < duration_s:
            loop_at = time.monotonic()
            if loop_at - last_renew >= RENEW_EVERY_S:
                _refresh(rec, driver.renew(sid)["data"])
                last_renew = time.monotonic()
                _log(adb, f"lease renewed · {rec.lease_remaining_ms} ms")
            if taps and loop_at - last_tap >= tap_every_s and rec.target_id:
                rec.last_action = _tap_increment(adb, driver, sid, rec.target_id)
                last_tap = time.monotonic()
            _refresh(rec, driver.inspect(sid)["data"])
            registry.write(rec)
            if rec.state != "active":
                _log(adb, f"session no longer active ({rec.state}); leaving the loop")
                rc = 1
                break
            next_due = min(last_renew + RENEW_EVERY_S, (last_tap + tap_every_s) if taps else float("inf"),
                           time.monotonic() + INSPECT_EVERY_S)
            time.sleep(max(0.2, min(INSPECT_EVERY_S, next_due - time.monotonic())))
    except KeyboardInterrupt:
        _log(adb, "interrupted; stopping the session")
    except CuaError as exc:
        _log(adb, f"driver error: {exc}")
        rec.state = "lost"
        rc = 1
    finally:
        at = time.time()
        try:
            stopped = driver.stop(sid)["data"]
            rec.state = "stopped"
            rec.last_action = {"kind": "stop", "at": at, "result": "ok", "detail": {"cleanup": stopped.get("cleanup")}}
            _log(adb, f"session {sid[:8]} stopped · cleanup {stopped.get('cleanup')}")
        except CuaError as exc:
            rec.state = "lost" if rec.state != "stopped" else rec.state
            rec.last_action = {"kind": "stop", "at": at, "result": f"{exc.status}:{exc.reason}", "detail": {}}
            _log(adb, f"session stop failed: {exc}")
            rc = rc or 1
        rec.updated_at = time.time()
        registry.write(rec)
    return rc
