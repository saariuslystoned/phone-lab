#!/usr/bin/env python3
"""Verify a running phone-lab viewer against a live Cua fixture session and print a JSON verdict.

Standard library only. Never starts or stops the server or the demo. Never prints the device serial.
Exit codes: 0 pass, 1 fail, 2 setup problem (server unreachable, serial ambiguous).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FIXTURE = "ai.cua.fixture.notes"
SCHEMA = "phone-lab.freeze.v1"


class Verdict:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.numbers: dict = {}

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return ok

    @property
    def passed(self) -> bool:
        return all(c["ok"] for c in self.checks)

    def to_json(self) -> dict:
        return {"result": "pass" if self.passed else "fail", "checks": self.checks, "numbers": self.numbers}


def get_json(url: str, timeout: float = 10, method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def resolve_serial(explicit: str | None, model: str | None = None, allow_emulators: bool = False) -> tuple[str | None, str]:
    """The serial without printing it: --serial, ANDROID_SERIAL, else the single physical device.

    Mirrors phonelab.adb.Adb.resolve: other agents' emulators (emulator-* serials) are never chosen
    implicitly, and --model narrows by model name.
    """
    if explicit:
        return explicit, "flag"
    if os.environ.get("ANDROID_SERIAL"):
        return os.environ["ANDROID_SERIAL"], "env"
    try:
        rows = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=15).stdout.splitlines()[1:]
    except (OSError, subprocess.TimeoutExpired):
        return None, "adb unavailable"
    devices = []
    for row in rows:
        parts = row.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        fields = dict(t.split(":", 1) for t in parts[2:] if ":" in t)
        devices.append({"serial": parts[0], "model": fields.get("model", "unknown").replace("_", " "),
                        "kind": "emulator" if parts[0].startswith("emulator-") else "physical"})
    pool = devices
    if model:
        pool = [d for d in pool if d["model"].casefold() == model.replace("_", " ").strip().casefold()]
    if not allow_emulators:
        pool = [d for d in pool if d["kind"] == "physical"]
    seen = ", ".join(f"{d['model']} ({d['kind']})" for d in devices) or "none"
    if len(pool) == 1:
        return pool[0]["serial"], "model match" if model else "single physical device"
    if not pool:
        return None, f"no matching physical device; seen: {seen}"
    return None, f"{len(pool)} devices match ({seen}); pass --serial or --model"


def stats(values: list[float]) -> dict | None:
    if not values:
        return None
    return {"n": len(values), "min": round(min(values), 2), "median": round(statistics.median(values), 2), "max": round(max(values), 2)}


def sample(base: str, duration: float, interval: float, verdict: Verdict, require_agent: bool, min_fps: float, min_ok_taps: int) -> dict:
    per: dict[str, dict] = {}
    taps: dict[float, str] = {}
    leases: list[int] = []
    errors = 0
    samples = 0
    stale_retries = 0
    first_agent_at: float | None = None
    started = time.time()
    end = started + duration
    while time.time() < end:
        t = time.time()
        try:
            state = get_json(base + "/api/state", timeout=5)
            samples += 1
        except Exception as exc:  # noqa: BLE001 — any transport error counts as a missed sample
            errors += 1
            time.sleep(max(0.0, interval - (time.time() - t)))
            continue
        for d in state.get("displays", []):
            key = f"{d.get('name')}/logical {d.get('logical_id')}"
            p = per.setdefault(key, {"role": d.get("role"), "states": set(), "fps": [], "capture_ms": [], "errors": set(), "seq_max": 0})
            p["states"].add(d.get("state"))
            if d.get("seq") is not None:
                p["fps"].append(float(d.get("fps") or 0))
                p["capture_ms"].append(int(d.get("capture_ms") or 0))
                p["seq_max"] = max(p["seq_max"], int(d["seq"]))
            if d.get("error") and d.get("state") != "OFF":
                p["errors"].add(str(d["error"]))
            session = d.get("session") or {}
            if d.get("role") == "agent" and session.get("state") not in (None, "unknown"):
                if first_agent_at is None:
                    first_agent_at = time.time()
                leases.append(int(session.get("lease_remaining_now_ms") or 0))
                action = session.get("last_action") or {}
                if action.get("kind") == "tap increment" and action.get("at") is not None:
                    taps[float(action["at"])] = str(action.get("result"))
                    stale_retries = max(stale_retries, int((action.get("detail") or {}).get("frame_stale_retries") or 0))
        time.sleep(max(0.0, interval - (time.time() - t)))
    numbers = {"samples": samples, "sample_errors": errors, "displays": {}}
    for key, p in per.items():
        numbers["displays"][key] = {"role": p["role"], "states": sorted(s for s in p["states"] if s), "fps": stats(p["fps"]),
                                    "capture_ms": stats(p["capture_ms"]), "seq_max": p["seq_max"], "errors": sorted(p["errors"])}
    ok_taps = sum(1 for r in taps.values() if r == "ok")
    numbers["taps"] = {"distinct": len(taps), "ok": ok_taps, "results": {r: list(taps.values()).count(r) for r in set(taps.values())},
                       "max_frame_stale_retries": stale_retries}
    numbers["lease_ms"] = {"min": min(leases), "max": max(leases)} if leases else None
    verdict.check("state samples collected", samples >= 3 and errors == 0, f"{samples} samples, {errors} errors")
    human0 = next((p for k, p in per.items() if p["role"] == "human" and k.endswith("logical 0")), None)
    agents = {k: p for k, p in per.items() if p["role"] == "agent"}
    verdict.check("display 0 captured", bool(human0 and human0["fps"]), "" if human0 else "no display with logical id 0")
    if human0 and human0["fps"]:
        med = statistics.median(human0["fps"])
        verdict.check(f"display 0 fps >= {min_fps}", med >= min_fps, f"median {med:.2f}, capture_ms median {statistics.median(human0['capture_ms']):.0f}")
    if require_agent:
        verdict.check("agent display present with a phone-lab session", bool(agents) and first_agent_at is not None,
                      ", ".join(agents) or "none")
        for key, p in agents.items():
            if p["fps"]:
                med = statistics.median(p["fps"])
                verdict.check(f"{key} fps >= {min_fps}", med >= min_fps, f"median {med:.2f}, capture_ms median {statistics.median(p['capture_ms']):.0f}")
        verdict.check(f"at least {min_ok_taps} tap results ok", ok_taps >= min_ok_taps, f"{ok_taps} ok of {len(taps)} distinct")
        verdict.check("lease never 0", bool(leases) and min(leases) > 0, f"min {min(leases) if leases else 'n/a'} ms")
    return numbers


def check_freeze(base: str, verdict: Verdict) -> dict:
    try:
        resp = get_json(base + "/api/freeze", timeout=60, method="POST")
    except Exception as exc:  # noqa: BLE001
        verdict.check("POST /api/freeze", False, str(exc)[:200])
        return {}
    image, manifest_path = Path(resp.get("image", "")), Path(resp.get("manifest", ""))
    verdict.check("freeze files exist", image.is_file() and manifest_path.is_file(), f"{image} {manifest_path}")
    if not manifest_path.is_file():
        return {"response": resp}
    m = json.loads(manifest_path.read_text())
    verdict.check("manifest schema", m.get("schema") == SCHEMA, str(m.get("schema")))
    panels = m.get("panels", [])
    verdict.check("manifest panel count 2 or 3", len(panels) in (2, 3), str(len(panels)))
    for p in panels:
        tag = f"{p.get('name')}/logical {p.get('logical_id')}"
        if p.get("seq") is not None:
            verdict.check(f"{tag} sha256 length", bool(re.fullmatch(r"[0-9a-f]{64}", str(p.get("png_sha256") or ""))), str(p.get("png_sha256") or "")[:12])
        if p.get("role") == "human" and p.get("logical_id") == 0:
            verdict.check("inner panel cropped_status_bar_px", int(p.get("cropped_status_bar_px") or 0) > 0, str(p.get("cropped_status_bar_px")))
    size = image.stat().st_size if image.is_file() else 0
    verdict.check("composite larger than 20 KB", size > 20_000, f"{size} bytes")
    return {"response": resp, "panels": len(panels), "image_bytes": size}


def privacy_grep(serial: str, repo: Path, runs_dir: Path, verdict: Verdict) -> None:
    hits = 0
    scanned = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in (".git", "runs", "__pycache__", "node_modules", ".venv") and not d.endswith("-runs")]
        for name in files:
            path = Path(root) / name
            try:
                if serial.encode() in path.read_bytes():
                    hits += 1
                    print(f"serial found in {path}", file=sys.stderr)
                scanned += 1
            except OSError:
                continue
    verdict.check("serial absent from the checkout", hits == 0, f"{scanned} files scanned, {hits} hits")
    mhits = 0
    manifests = list(runs_dir.glob("*/freeze-*.json")) + list((runs_dir / "sessions").glob("*.json"))
    for path in manifests:
        try:
            if serial.encode() in path.read_bytes():
                mhits += 1
                print(f"serial found in {path}", file=sys.stderr)
        except OSError:
            continue
    verdict.check("serial absent from run manifests and registry", mhits == 0, f"{len(manifests)} files, {mhits} hits")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:8791")
    ap.add_argument("--duration", type=float, default=120, help="seconds to sample /api/state")
    ap.add_argument("--interval", type=float, default=5)
    ap.add_argument("--min-fps", type=float, default=0.8)
    ap.add_argument("--min-ok-taps", type=int, default=10)
    ap.add_argument("--runs-dir", default="runs/phone-lab-runs")
    ap.add_argument("--require-agent", dest="require_agent", action="store_true", default=True)
    ap.add_argument("--no-require-agent", dest="require_agent", action="store_false")
    ap.add_argument("--skip-freeze", action="store_true")
    ap.add_argument("--serial", help="device serial for the privacy grep (never printed); also ANDROID_SERIAL")
    ap.add_argument("--model", help='pick the device by model name, e.g. "Pixel 10 Pro Fold"')
    ap.add_argument("--allow-emulators", action="store_true", help="allow an emulator-* device to be chosen implicitly")
    ap.add_argument("--repo", default=".", help="checkout to grep for the serial")
    ap.add_argument("--output", help="also write the verdict JSON here")
    args = ap.parse_args(argv)
    base = args.base_url.rstrip("/")
    verdict = Verdict()
    try:
        state = get_json(base + "/api/state", timeout=5)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"result": "setup", "error": f"viewer unreachable at {base}: {exc}",
                          "start": ["python3 -m phonelab cua demo --driver <cua-driver> --duration 900 --tap-every 8 [--serial S]",
                                    "python3 -m phonelab serve --port 8791 [--serial S]"]}, indent=1))
        return 2
    serial, source = resolve_serial(args.serial, args.model, args.allow_emulators)
    if not serial:
        print(json.dumps({"result": "setup", "error": f"cannot determine the device serial for the privacy grep ({source})"}, indent=1))
        return 2
    verdict.numbers["device"] = state.get("device")
    verdict.numbers["serial_source"] = source
    verdict.numbers["sample"] = sample(base, args.duration, args.interval, verdict, args.require_agent, args.min_fps, args.min_ok_taps)
    if not args.skip_freeze:
        verdict.numbers["freeze"] = check_freeze(base, verdict)
    privacy_grep(serial, Path(args.repo), Path(args.runs_dir), verdict)
    out = verdict.to_json()
    text = json.dumps(out, indent=1)
    if serial in text:
        text = text.replace(serial, "<serial>")
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n")
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
