"""Trail model: schemas, load/save/validate, script parsing, sha256 (no device)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

TRAIL_SCHEMA = "phone-lab.trail.v1"
ACTION_KINDS = ("launch", "tap", "set_text", "key", "swipe", "wait_for", "sleep")
PREDICATE_KINDS = ("fixture_counter", "text_present", "ref_present", "ref_absent")
ALLOWED_PACKAGES = ("ai.cua.fixture.notes", "ai.cua.android.demo")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
DEFAULT_TIMEOUT_MS = 5000


class TrailError(Exception):
    """Trail validation, parsing, or loading error. Message is safe to print."""


@dataclass
class Step:
    name: str
    display: str
    action: dict
    predicate: dict | None

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "display": self.display,
            "action": self.action,
            "predicate": self.predicate,
        }


@dataclass
class Trail:
    name: str
    created_at: str
    recorded_on: dict | None
    session: dict
    steps: list[Step]

    def to_json(self) -> dict:
        return {
            "schema": TRAIL_SCHEMA,
            "name": self.name,
            "created_at": self.created_at,
            "recorded_on": self.recorded_on,
            "session": self.session,
            "steps": [s.to_json() for s in self.steps],
        }

    @staticmethod
    def from_json(data: dict) -> "Trail":
        if not isinstance(data, dict):
            raise TrailError("trail root must be a JSON object")
        schema = data.get("schema")
        if schema != TRAIL_SCHEMA:
            raise TrailError(f"invalid schema {schema!r}, expected {TRAIL_SCHEMA!r}")
        name = data.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise TrailError(f"invalid trail name: {name!r}")
        created_at = data.get("created_at")
        if not isinstance(created_at, str):
            raise TrailError(f"invalid created_at: {created_at!r}")
        recorded_on = data.get("recorded_on")
        if recorded_on is not None and not isinstance(recorded_on, dict):
            raise TrailError("recorded_on must be an object or null")
        session = data.get("session")
        if not isinstance(session, dict):
            raise TrailError("session must be an object")
        allow_apps = session.get("allow_apps")
        if not isinstance(allow_apps, list) or not allow_apps:
            raise TrailError("session.allow_apps must be a non-empty list")
        for app in allow_apps:
            if not isinstance(app, str) or app not in ALLOWED_PACKAGES:
                raise TrailError(f"package {app!r} not in allowed packages: {ALLOWED_PACKAGES}")
        label = session.get("label")
        if not isinstance(label, str):
            raise TrailError("session.label must be a string")

        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            raise TrailError("steps must be a list")
        steps = []
        for i, s_data in enumerate(raw_steps):
            if not isinstance(s_data, dict):
                raise TrailError(f"step {i}: step must be an object")
            step_name = s_data.get("name")
            if not isinstance(step_name, str):
                raise TrailError(f"step {i}: name must be a string")
            display = s_data.get("display")
            if display != "agent":
                raise TrailError(f"step {i}: invalid display {display!r}, must be 'agent'")
            try:
                action = validate_action(s_data.get("action"))
            except TrailError as exc:
                raise TrailError(f"step {i}: action invalid: {exc}") from None
            try:
                predicate = validate_predicate(s_data.get("predicate"))
            except TrailError as exc:
                raise TrailError(f"step {i}: predicate invalid: {exc}") from None
            steps.append(Step(name=step_name, display=display, action=action, predicate=predicate))
        return Trail(name=name, created_at=created_at, recorded_on=recorded_on, session=session, steps=steps)


def validate_action(action: dict) -> dict:
    if not isinstance(action, dict):
        raise TrailError("action must be an object")
    kind = action.get("kind")
    if kind not in ACTION_KINDS:
        raise TrailError(f"unknown action kind: {kind!r}")
    res = dict(action)
    if kind == "launch":
        pkg = res.get("package")
        if not isinstance(pkg, str) or pkg not in ALLOWED_PACKAGES:
            raise TrailError(f"launch package {pkg!r} not allowed")
        res["fresh"] = bool(res.get("fresh", True))
    elif kind == "tap":
        ref = res.get("ref")
        if not isinstance(ref, str) or not ref:
            raise TrailError("tap requires a non-empty ref")
    elif kind == "set_text":
        ref = res.get("ref")
        if not isinstance(ref, str) or not ref:
            raise TrailError("set_text requires a non-empty ref")
        text = res.get("text")
        if not isinstance(text, str):
            raise TrailError("set_text requires string text")
        res["clear_first"] = bool(res.get("clear_first", True))
    elif kind == "key":
        keycode = res.get("keycode")
        if not isinstance(keycode, str) or not keycode:
            raise TrailError("key requires a non-empty keycode")
    elif kind == "swipe":
        frm = res.get("from")
        to = res.get("to")
        if not isinstance(frm, list) or len(frm) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in frm):
            raise TrailError("swipe requires 'from' as [x, y] ints")
        if not isinstance(to, list) or len(to) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in to):
            raise TrailError("swipe requires 'to' as [x, y] ints")
        res["duration_ms"] = int(res.get("duration_ms", 300))
    elif kind == "sleep":
        ms = res.get("ms")
        if not isinstance(ms, int) or isinstance(ms, bool) or ms <= 0:
            raise TrailError("sleep requires ms > 0 integer")
    elif kind == "wait_for":
        pass
    return res


def validate_predicate(pred: dict | None) -> dict | None:
    if pred is None:
        return None
    if not isinstance(pred, dict):
        raise TrailError("predicate must be an object or null")
    kind = pred.get("kind")
    if kind not in PREDICATE_KINDS:
        raise TrailError(f"unknown predicate kind: {kind!r}")
    res = dict(pred)
    if kind == "fixture_counter":
        exp = res.get("expected")
        if not isinstance(exp, int) or isinstance(exp, bool):
            raise TrailError("fixture_counter requires integer expected")
    elif kind == "text_present":
        txt = res.get("text")
        if not isinstance(txt, str) or not txt:
            raise TrailError("text_present requires non-empty text")
    elif kind in ("ref_present", "ref_absent"):
        ref = res.get("ref")
        if not isinstance(ref, str) or not ref:
            raise TrailError(f"{kind} requires non-empty ref")

    timeout_ms = res.get("timeout_ms")
    if timeout_ms is None:
        res["timeout_ms"] = DEFAULT_TIMEOUT_MS
    else:
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
            raise TrailError("timeout_ms must be a positive integer")
    return res


def load_trail(path: Path) -> Trail:
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise TrailError(f"cannot load trail from {path}: {exc}") from None
    return Trail.from_json(data)


def save_trail(trail: Trail, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(trail.to_json(), indent=2) + "\n")
    os.replace(tmp, path)


def trail_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def default_label(name: str) -> str:
    return f"phone-lab trail {name}"


def _default_step_name(action: dict, predicate: dict | None) -> str:
    kind = action.get("kind", "")
    if kind == "launch":
        pkg = action.get("package", "")
        return "launch fixture" if pkg == "ai.cua.fixture.notes" else f"launch {pkg}"
    if kind == "tap":
        return f"tap {action.get('ref', '')}"
    if kind == "wait_for":
        if predicate and predicate.get("kind") == "text_present":
            return f"wait for {predicate.get('text')}"
        return "wait_for"
    if kind == "set_text":
        return f"set_text {action.get('ref', '')}"
    if kind == "key":
        return f"key {action.get('keycode', '')}"
    if kind == "swipe":
        return "swipe"
    if kind == "sleep":
        return f"sleep {action.get('ms', '')}ms"
    return kind


def parse_script_line(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        raise TrailError(f"syntax error in line: {exc}") from None
    if not tokens:
        return None

    if tokens[0] == "name":
        if len(tokens) < 2:
            raise TrailError("name line requires text")
        return {"kind": "name", "name": " ".join(tokens[1:])}

    timeout_ms = None
    if "--timeout-ms" in tokens:
        idx = tokens.index("--timeout-ms")
        if idx + 1 >= len(tokens):
            raise TrailError("missing value for --timeout-ms")
        try:
            timeout_ms = int(tokens[idx + 1])
        except ValueError:
            raise TrailError(f"invalid integer for --timeout-ms: {tokens[idx + 1]}")
        tokens = tokens[:idx] + tokens[idx + 2:]

    kind = tokens[0]
    if kind not in ACTION_KINDS:
        raise TrailError(f"unknown action kind: {kind!r}")

    explicit_predicate = False
    pred_dict: dict | None = None

    if "expect" in tokens:
        exp_idx = tokens.index("expect")
        pred_tokens = tokens[exp_idx + 1:]
        tokens = tokens[:exp_idx]
        if not pred_tokens:
            raise TrailError("expect requires a predicate")
        pred_kind = pred_tokens[0]
        if pred_kind not in PREDICATE_KINDS:
            raise TrailError(f"unknown predicate kind in expect: {pred_kind!r}")
        if pred_kind == "fixture_counter":
            if len(pred_tokens) < 2:
                raise TrailError("fixture_counter requires expected count")
            try:
                expected = int(pred_tokens[1])
            except ValueError:
                raise TrailError(f"fixture_counter requires integer, got: {pred_tokens[1]}")
            pred_dict = {"kind": pred_kind, "expected": expected}
        elif pred_kind == "text_present":
            if len(pred_tokens) < 2:
                raise TrailError("text_present requires text")
            pred_dict = {"kind": pred_kind, "text": pred_tokens[1]}
        elif pred_kind in ("ref_present", "ref_absent"):
            if len(pred_tokens) < 2:
                raise TrailError(f"{pred_kind} requires ref")
            pred_dict = {"kind": pred_kind, "ref": pred_tokens[1]}
        if timeout_ms is not None:
            pred_dict["timeout_ms"] = timeout_ms
        explicit_predicate = True

    if kind == "launch":
        fresh = True
        if "--keep" in tokens:
            fresh = False
            tokens.remove("--keep")
        if len(tokens) < 2:
            raise TrailError("launch requires package name")
        package = tokens[1]
        action = {"kind": "launch", "package": package, "fresh": fresh}
    elif kind == "tap":
        if len(tokens) < 2:
            raise TrailError("tap requires ref")
        ref = tokens[1]
        action = {"kind": "tap", "ref": ref}
    elif kind == "set_text":
        clear_first = True
        if "--no-clear" in tokens:
            clear_first = False
            tokens.remove("--no-clear")
        if len(tokens) < 3:
            raise TrailError("set_text requires ref and text")
        ref = tokens[1]
        text = tokens[2]
        action = {"kind": "set_text", "ref": ref, "text": text, "clear_first": clear_first}
    elif kind == "key":
        if len(tokens) < 2:
            raise TrailError("key requires keycode")
        keycode = tokens[1]
        action = {"kind": "key", "keycode": keycode}
    elif kind == "swipe":
        if len(tokens) < 5:
            raise TrailError("swipe requires x1 y1 x2 y2")
        try:
            x1, y1, x2, y2 = int(tokens[1]), int(tokens[2]), int(tokens[3]), int(tokens[4])
        except ValueError:
            raise TrailError("swipe coordinates must be integers")
        duration_ms = 300
        if len(tokens) >= 6:
            try:
                duration_ms = int(tokens[5])
            except ValueError:
                raise TrailError("swipe duration must be integer")
        action = {"kind": "swipe", "from": [x1, y1], "to": [x2, y2], "duration_ms": duration_ms}
    elif kind == "sleep":
        if len(tokens) < 2:
            raise TrailError("sleep requires ms")
        try:
            ms = int(tokens[1])
        except ValueError:
            raise TrailError("sleep requires integer ms")
        action = {"kind": "sleep", "ms": ms}
    elif kind == "wait_for":
        pred_tokens = tokens[1:]
        if not pred_tokens:
            raise TrailError("wait_for requires a predicate")
        pred_kind = pred_tokens[0]
        if pred_kind not in PREDICATE_KINDS:
            raise TrailError(f"unknown predicate kind in wait_for: {pred_kind!r}")
        if pred_kind == "fixture_counter":
            if len(pred_tokens) < 2:
                raise TrailError("fixture_counter requires expected count")
            try:
                expected = int(pred_tokens[1])
            except ValueError:
                raise TrailError(f"fixture_counter requires integer, got: {pred_tokens[1]}")
            pred_dict = {"kind": pred_kind, "expected": expected}
        elif pred_kind == "text_present":
            if len(pred_tokens) < 2:
                raise TrailError("text_present requires text")
            pred_dict = {"kind": pred_kind, "text": pred_tokens[1]}
        elif pred_kind in ("ref_present", "ref_absent"):
            if len(pred_tokens) < 2:
                raise TrailError(f"{pred_kind} requires ref")
            pred_dict = {"kind": pred_kind, "ref": pred_tokens[1]}
        if timeout_ms is not None:
            pred_dict["timeout_ms"] = timeout_ms
        action = {"kind": "wait_for"}
        explicit_predicate = True

    action = validate_action(action)
    if pred_dict is not None:
        pred_dict = validate_predicate(pred_dict)

    return {
        "kind": kind,
        "action": action,
        "predicate": pred_dict,
        "name": None,
        "timeout_ms": timeout_ms,
        "explicit_predicate": explicit_predicate,
    }


def parse_script(text: str) -> list[dict]:
    steps: list[dict] = []
    pending_name: str | None = None
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parsed = parse_script_line(line)
        except TrailError as exc:
            raise TrailError(f"line {line_no}: {exc}") from None
        if parsed is None:
            continue
        if parsed["kind"] == "name":
            pending_name = parsed["name"]
            continue
        if pending_name is not None:
            parsed["name"] = pending_name
            pending_name = None
        else:
            parsed["name"] = _default_step_name(parsed["action"], parsed["predicate"])
        steps.append(parsed)
    return steps


def derive_predicate(kind: str, action: dict, observed: dict | None = None) -> dict | None:
    """Derive non-oracle predicate: set_text -> text_present, tap/key/swipe with a ref -> ref_present, else None."""
    obs = observed or {}
    timeout_ms = obs.get("timeout_ms", DEFAULT_TIMEOUT_MS)

    if kind == "set_text":
        text = action.get("text")
        if text:
            return {"kind": "text_present", "text": text, "timeout_ms": timeout_ms}
        ref = action.get("ref")
        if ref:
            return {"kind": "ref_present", "ref": ref, "timeout_ms": timeout_ms}
        return None
    if kind in ("tap", "key", "swipe"):
        ref = action.get("ref")
        if ref:
            return {"kind": "ref_present", "ref": ref, "timeout_ms": timeout_ms}
        return None
    return None
