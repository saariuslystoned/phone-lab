#!/usr/bin/env python3
"""phone-lab run directories -> Journeys-style results markdown (stdlib only).

Steps are grouped back into journey actions by their name: a step named
exactly like the action sentence, or `<sentence> [k/n]` when one action
compiled to several steps.

    python3 skills/phone-lab-journey/scripts/journey_results.py \
        --journey journey.xml RUN_DIR [RUN_DIR ...] [-o results.md]

Exit 0 = written and every run passed, 1 = written and something failed or
was not evaluated, 2 = bad input (missing run.json, malformed journey).
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from journey_to_script import JourneyError, parse_journey  # noqa: E402

PART_RE = re.compile(r"\s*\[\d+/\d+\]$")
OK = ("ok", "healed")


class ResultsError(Exception):
    """Unreadable run directory. Message is safe to print."""


def base_name(step_name: str) -> str:
    return PART_RE.sub("", " ".join((step_name or "").split()))


def _q(text: str) -> str:
    return shlex.quote(text) if re.search(r"[\s\"'\\]", text) else text


def predicate_text(pred: dict | None) -> str:
    if not pred:
        return "none"
    kind = pred.get("kind")
    if kind == "fixture_counter":
        return f"fixture_counter {pred.get('expected')}"
    if kind == "text_present":
        return f"text_present {_q(pred.get('text', ''))}"
    if kind in ("ref_present", "ref_absent"):
        return f"{kind} {pred.get('ref')}"
    return str(kind)


def action_line(action: dict, pred: dict | None) -> str:
    """The trail-script line for a recorded step (refs, never coordinates, except swipe)."""
    kind = action.get("kind")
    target = action.get("ref") or (f"label:{_q(action['label'])}" if action.get("label") else "?")
    if kind == "launch":
        line = f"launch {action.get('package')}"
        if action.get("activity"):
            line += f" --activity {action['activity']}"
        return line + ("" if action.get("fresh", True) else " --keep")
    if kind == "tap":
        return f"tap {target}"
    if kind == "set_text":
        return f"set_text {target} {_q(action.get('text', ''))}" + ("" if action.get("clear_first", True) else " --no-clear")
    if kind == "key":
        return f"key {action.get('keycode')}"
    if kind == "swipe":
        f, t = action.get("from", ["?", "?"]), action.get("to", ["?", "?"])
        return f"swipe {f[0]} {f[1]} {t[0]} {t[1]} {action.get('duration_ms', 300)}"
    if kind == "sleep":
        return f"sleep {action.get('ms')}"
    if kind == "wait_for":
        return f"wait_for {predicate_text(pred)}"
    if kind == "resize":
        return f"resize {action.get('size')}" + (f" --density {action['density']}" if action.get("density") else "")
    return str(kind)


def load_run_dir(path: Path) -> dict:
    run_file = Path(path) / "run.json"
    try:
        run = json.loads(run_file.read_text())
    except (OSError, ValueError) as exc:
        raise ResultsError(f"cannot read {run_file}: {exc}") from None
    trail_steps: list[dict] = []
    try:
        trail_steps = json.loads((Path(path) / "trail.json").read_text()).get("steps") or []
    except (OSError, ValueError):
        pass
    steps = []
    for s in run.get("steps") or []:
        doc: dict = {}
        try:
            doc = json.loads((Path(path) / s.get("dir", "") / "step.json").read_text())
        except (OSError, ValueError):
            pass
        idx = s.get("index", len(steps))
        tstep = trail_steps[idx] if idx < len(trail_steps) else {}
        result = doc.get("result") or {}
        steps.append({
            "index": idx,
            "name": s.get("name") or doc.get("name") or "",
            "status": s.get("status") or result.get("status"),
            "duration_ms": s.get("duration_ms"),
            "message": result.get("message"),
            "line": action_line(tstep.get("action") or {"kind": s.get("kind")}, tstep.get("predicate")),
            "predicate": predicate_text(tstep.get("predicate")),
        })
    return {"dir": Path(path), "run": run, "steps": steps}


def label_runs(runs: list[dict]) -> list[dict]:
    runs = sorted(runs, key=lambda r: (r["run"].get("created_at") or "", r["dir"].name))
    n = 0
    for r in runs:
        if r["run"].get("kind") == "replay":
            n += 1
            r["label"] = f"replay {n}"
        else:
            r["label"] = r["run"].get("kind") or "run"
    return runs


def _mark(cells: list[dict | None]) -> tuple[str, list[str]]:
    """✅ every run ok, ❌ any run failed, '' otherwise (not evaluated somewhere)."""
    statuses = [c["status"] if c else None for c in cells]
    if any(s not in OK + ("skipped", None) for s in statuses):
        return " ❌", []
    if statuses and all(s in OK for s in statuses):
        return " ✅", []
    return "", ["not evaluated in every run (an earlier action failed)"]


def render(runs: list[dict], journey=None) -> tuple[str, bool]:
    runs = label_runs(runs)
    ref = runs[-1] if runs else None
    trail = (ref["run"].get("trail") or {}) if ref else {}
    device = (ref["run"].get("device") or {}) if ref else {}
    order: list[str] = list(journey.actions) if journey else []
    for r in runs:
        for s in r["steps"]:
            b = base_name(s["name"])
            if b not in order:
                order.append(b)

    title = journey.name if journey else trail.get("name", "journey")
    out = [f"# Journey: {title}", ""]
    if journey and journey.description:
        out += [journey.description, ""]
    out.append(f"- **Trail**: `{trail.get('name')}` (sha256 `{(trail.get('sha256') or '')[:12]}`)")
    dev = " · ".join(str(v) for v in (device.get("model"), device.get("android_release") and f"Android {device['android_release']}") if v)
    if dev:
        out.append(f"- **Device**: {dev}")
    out += ["", "| Run | Directory | Result | Steps ok | Total |", "|---|---|---|---|---|"]
    all_pass = bool(runs)
    for r in runs:
        res = r["run"].get("result") or {}
        total = (r["run"].get("timings") or {}).get("total_ms")
        status = res.get("status") or "unfinished"
        all_pass &= status == "pass"
        out.append(
            f"| {r['label']} | `{r['dir'].name}` | {status} | "
            f"{res.get('steps_ok', '?')}/{res.get('steps_total', '?')} | "
            f"{'%.1f s' % (total / 1000) if isinstance(total, (int, float)) else '?'} |"
        )
    out += ["", "## Results", ""]
    for sentence in order:
        steps_by_run = [[s for s in r["steps"] if base_name(s["name"]) == sentence] for r in runs]
        compiled = next((ss for ss in steps_by_run if ss), [])
        if not compiled:
            all_pass = False
            out += [f"### Action: {sentence} ❌", "- **Comment**: no compiled step carries this sentence as its name", ""]
            continue
        # One cell per run per step position; any missing step counts as not evaluated.
        cells = [ss[i] if i < len(ss) else None for ss in steps_by_run for i in range(len(compiled))]
        mark, comments = _mark(cells)
        all_pass &= mark == " ✅"
        out.append(f"### Action: {sentence}{mark}")
        out.append("- **Compiled**: " + " · ".join(f"`{s['line']}` (expect `{s['predicate']}`)" if s["line"].split()[0] != "wait_for" else f"`{s['line']}`" for s in compiled))
        per_run = []
        for r, ss in zip(runs, steps_by_run):
            if not ss:
                per_run.append(f"{r['label']} —")
                continue
            per_run.append(f"{r['label']} " + ", ".join(f"{s['status']} {s['duration_ms']} ms" for s in ss))
            for s in ss:
                if s["status"] == "healed":
                    comments.append(f"{r['label']}: healed ({s['message'] or 'see step.json'})")
                elif s["status"] not in OK + ("skipped",):
                    comments.append(f"{r['label']}: {s['status']} — {s['message'] or 'no message'}")
        out.append("- **Runs**: " + " · ".join(per_run))
        if comments:
            out.append("- **Comment**: " + "; ".join(comments))
        out.append("")
    out.append(f"**Overall: {'PASS' if all_pass else 'FAIL'}**")
    return "\n".join(out) + "\n", all_pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+", help="run directories (each holds run.json)")
    ap.add_argument("--journey", help="journey XML; orders and checks actions")
    ap.add_argument("-o", "--output", help="write markdown here (default stdout)")
    args = ap.parse_args(argv)
    try:
        journey = parse_journey(Path(args.journey).read_text()) if args.journey else None
        runs = [load_run_dir(Path(p)) for p in args.runs]
    except (JourneyError, ResultsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text, ok = render(runs, journey)
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
