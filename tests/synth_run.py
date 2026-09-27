"""Synthetic run generator for trace testing (small PIL images, no device)."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

RUN_SCHEMA = "phone-lab.run.v1"
STEP_SCHEMA = "phone-lab.step.v1"
TREE_SCHEMA = "phone-lab.tree.v1"


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")


def _node_ref(cls: str, label: str, cx: int, cy: int) -> str:
    return hashlib.sha1(f"{cls}{label}{cx},{cy}".encode()).hexdigest()[:6]


def _make_agent_image(size: tuple[int, int], count: int, btn_cx: int, btn_cy: int) -> bytes:
    img = Image.new("RGB", size, (22, 26, 42))
    draw = ImageDraw.Draw(img)
    draw.text((size[0] // 2 - 10, 25), str(count), fill=(230, 230, 240))
    draw.rectangle([btn_cx - 20, btn_cy - 12, btn_cx + 20, btn_cy + 12], fill=(45, 65, 110), outline=(85, 125, 210))
    draw.text((btn_cx - 4, btn_cy - 6), "+", fill=(240, 240, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_human_image(size: tuple[int, int], step_idx: int) -> bytes:
    img = Image.new("RGB", size, (34, 44, 58))
    draw = ImageDraw.Draw(img)
    draw.text((10, 15), f"H:{step_idx}", fill=(210, 215, 225))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_run(runs_dir: Path, run_id: str, *, steps: int = 5, kind: str = "replay", seed: int = 0,
             fail_at: int | None = None, drift_px: int = 0, size: tuple[int, int] = (108, 192),
             created_at: float = 1_790_000_000.0) -> Path:
    """Generate a valid, deterministic synthetic run directory."""
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    steps_dir = run_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)

    # Write trail.json
    trail_obj = {"name": "fixture-counter", "step_count": steps}
    trail_bytes = (json.dumps(trail_obj, indent=2) + "\n").encode()
    (run_dir / "trail.json").write_bytes(trail_bytes)
    trail_sha = hashlib.sha256(trail_bytes).hexdigest()

    btn_cx = (size[0] // 2) + drift_px
    btn_cy = (size[1] // 2) + 30 + drift_px
    btn_ref = _node_ref("android.widget.Button", "Increment", btn_cx, btn_cy)

    cur_time = created_at
    step_summaries = []
    has_failed = False

    for i in range(steps):
        s_dir = steps_dir / f"{i:03d}"
        s_dir.mkdir(parents=True, exist_ok=True)

        is_fail = fail_at is not None and i == fail_at
        is_skipped = fail_at is not None and i > fail_at

        if is_fail:
            has_failed = True
            step_status = "fail"
        elif is_skipped or has_failed:
            step_status = "skipped"
        else:
            step_status = "ok"

        step_name = "launch fixture" if i == 0 else f"tap increment {i}"
        step_kind = "launch" if i == 0 else "tap"

        if step_status == "skipped":
            duration_ms = 0
            timings = {
                "started_at": round(cur_time, 3),
                "finished_at": round(cur_time, 3),
                "duration_ms": 0,
                "phases": {},
            }
            captures = {"before": [], "after": []}
            trees = {"before": {}, "after": {}}
            action = {
                "kind": step_kind,
                "display_id": 98,
                "ref": btn_ref if step_kind == "tap" else None,
                "detail": {"x": btn_cx, "y": btn_cy, "snapshot_id": f"snap-{seed}-{i}", "package": "ai.cua.fixture.notes"}
                if step_kind == "tap" else {"package": "ai.cua.fixture.notes"},
            }
            predicate = {
                "kind": "fixture_counter",
                "display_id": 98,
                "timeout_ms": 5000,
                "expected": i,
            } if i > 0 else None
            result = {
                "status": "skipped",
                "message": "skipped after earlier failure",
                "detail": {},
            }
        else:
            p_cap_before = 200 + (seed * 11 + i * 17) % 50
            p_tree_before = 50 + (seed * 7 + i * 13) % 20
            p_action = 150 + (seed * 19 + i * 29) % 40
            p_wait = 100 + (seed * 3 + i * 31) % 30
            p_cap_after = 180 + (seed * 5 + i * 7) % 40
            p_tree_after = 40 + (seed * 13 + i * 11) % 20
            phases = {
                "capture_before_ms": p_cap_before,
                "tree_before_ms": p_tree_before,
                "action_ms": p_action,
                "wait_ms": p_wait,
                "capture_after_ms": p_cap_after,
                "tree_after_ms": p_tree_after,
            }
            duration_ms = sum(phases.values())
            step_start = cur_time
            step_end = cur_time + duration_ms / 1000.0
            timings = {
                "started_at": round(step_start, 3),
                "finished_at": round(step_end, 3),
                "duration_ms": duration_ms,
                "phases": phases,
            }
            cur_time = step_end

            cnt_before = max(0, i - 1) if i > 0 else 0
            cnt_after = cnt_before if is_fail else (i if i > 0 else 0)

            # Generate PNGs
            human_before_png = _make_human_image((120, 180), i)
            human_after_png = _make_human_image((120, 180), i)
            agent_before_png = _make_agent_image(size, cnt_before, btn_cx, btn_cy)
            agent_after_png = _make_agent_image(size, cnt_after, btn_cx, btn_cy)

            (s_dir / "before-logical-0.png").write_bytes(human_before_png)
            (s_dir / "after-logical-0.png").write_bytes(human_after_png)
            (s_dir / "before-logical-98.png").write_bytes(agent_before_png)
            (s_dir / "after-logical-98.png").write_bytes(agent_after_png)

            sha_h_b = hashlib.sha256(human_before_png).hexdigest()
            sha_h_a = hashlib.sha256(human_after_png).hexdigest()
            sha_a_b = hashlib.sha256(agent_before_png).hexdigest()
            sha_a_a = hashlib.sha256(agent_after_png).hexdigest()

            cap_h_b = {
                "sf_id": "1", "unique_id": "local:1", "logical_id": 0, "name": "Inner Display",
                "role": "human", "seq": 100 + i * 2, "captured_at": round(step_start, 3),
                "capture_ms": 40, "width": 120, "height": 180, "png_sha256": sha_h_b,
                "cropped_status_bar_px": 20, "image": "before-logical-0.png", "session": None,
            }
            cap_a_b = {
                "sf_id": "2", "unique_id": "virtual:com.android.shell,2000,Cua agent,90", "logical_id": 98,
                "name": "Cua agent", "role": "agent", "seq": 100 + i * 2 + 1, "captured_at": round(step_start + 0.05, 3),
                "capture_ms": 35, "width": size[0], "height": size[1], "png_sha256": sha_a_b,
                "cropped_status_bar_px": 0, "image": "before-logical-98.png",
                "session": {
                    "session_id": "synth-session-001", "label": "phone-lab demo", "display_id": 98,
                    "package": "ai.cua.fixture.notes", "target_id": "synth-target", "state": "active",
                    "lease_remaining_ms": 60000, "lease_checked_at": step_start,
                    "last_action": {"kind": step_kind, "at": step_start, "result": "ok", "detail": {}},
                    "owner": "phonelab synth", "updated_at": step_start, "lease_remaining_now_ms": 55000,
                },
            }
            cap_off = {
                "sf_id": "3", "unique_id": "local:3", "logical_id": 3, "name": "Outer Display",
                "role": "human", "seq": None, "captured_at": None, "capture_ms": None,
                "width": 60, "height": 120, "png_sha256": None, "cropped_status_bar_px": 0,
                "image": None, "session": None,
            }
            cap_h_a = dict(cap_h_b, seq=100 + i * 2 + 10, captured_at=round(step_end - 0.05, 3),
                           png_sha256=sha_h_a, image="after-logical-0.png")
            cap_a_a = dict(cap_a_b, seq=100 + i * 2 + 11, captured_at=round(step_end, 3),
                           png_sha256=sha_a_a, image="after-logical-98.png")

            captures = {
                "before": [cap_h_b, cap_a_b, cap_off],
                "after": [cap_h_a, cap_a_a, cap_off],
            }

            # Generate trees for agent display
            cnt_ref_b = _node_ref("android.widget.TextView", f"Count: {cnt_before}", size[0] // 2, 35)
            cnt_ref_a = _node_ref("android.widget.TextView", f"Count: {cnt_after}", size[0] // 2, 35)

            tree_before = {
                "schema": TREE_SCHEMA,
                "display_id": 98,
                "captured_at": round(step_start + 0.1, 3),
                "read_ms": p_tree_before,
                "package": "ai.cua.fixture.notes",
                "window_count": 1,
                "nodes": [
                    {"i": 0, "parent": None, "depth": 0, "ref": None, "class": "android.widget.FrameLayout",
                     "text": None, "content_desc": None, "resource_id": None, "bounds": [0, 0, size[0], size[1]],
                     "clickable": False, "enabled": True, "focused": False},
                    {"i": 1, "parent": 0, "depth": 1, "ref": cnt_ref_b, "class": "android.widget.TextView",
                     "text": f"Count: {cnt_before}", "content_desc": None, "resource_id": "ai.cua.fixture.notes:id/counter",
                     "bounds": [10, 20, size[0] - 10, 50], "clickable": False, "enabled": True, "focused": False},
                    {"i": 2, "parent": 0, "depth": 1, "ref": btn_ref, "class": "android.widget.Button",
                     "text": "+", "content_desc": "Increment", "resource_id": "ai.cua.fixture.notes:id/increment",
                     "bounds": [btn_cx - 20, btn_cy - 12, btn_cx + 20, btn_cy + 12], "clickable": True,
                     "enabled": True, "focused": False},
                ],
            }
            tree_after = {
                "schema": TREE_SCHEMA,
                "display_id": 98,
                "captured_at": round(step_end - 0.02, 3),
                "read_ms": p_tree_after,
                "package": "ai.cua.fixture.notes",
                "window_count": 1,
                "nodes": [
                    {"i": 0, "parent": None, "depth": 0, "ref": None, "class": "android.widget.FrameLayout",
                     "text": None, "content_desc": None, "resource_id": None, "bounds": [0, 0, size[0], size[1]],
                     "clickable": False, "enabled": True, "focused": False},
                    {"i": 1, "parent": 0, "depth": 1, "ref": cnt_ref_a, "class": "android.widget.TextView",
                     "text": f"Count: {cnt_after}", "content_desc": None, "resource_id": "ai.cua.fixture.notes:id/counter",
                     "bounds": [10, 20, size[0] - 10, 50], "clickable": False, "enabled": True, "focused": False},
                    {"i": 2, "parent": 0, "depth": 1, "ref": btn_ref, "class": "android.widget.Button",
                     "text": "+", "content_desc": "Increment", "resource_id": "ai.cua.fixture.notes:id/increment",
                     "bounds": [btn_cx - 20, btn_cy - 12, btn_cx + 20, btn_cy + 12], "clickable": True,
                     "enabled": True, "focused": False},
                ],
            }

            (s_dir / "tree-before-logical-98.json").write_text(json.dumps(tree_before, indent=2) + "\n")
            (s_dir / "tree-after-logical-98.json").write_text(json.dumps(tree_after, indent=2) + "\n")

            trees = {
                "before": {"logical-98": "tree-before-logical-98.json"},
                "after": {"logical-98": "tree-after-logical-98.json"},
            }

            action = {
                "kind": step_kind,
                "display_id": 98,
                "ref": btn_ref if step_kind == "tap" else None,
                "detail": {"x": btn_cx, "y": btn_cy, "snapshot_id": f"snap-{seed}-{i}", "package": "ai.cua.fixture.notes"}
                if step_kind == "tap" else {"package": "ai.cua.fixture.notes"},
            }
            predicate = {
                "kind": "fixture_counter",
                "display_id": 98,
                "timeout_ms": 5000,
                "expected": i,
            } if i > 0 else None
            result = {
                "status": step_status,
                "message": f"step {i} failed: predicate timeout" if is_fail else None,
                "detail": {"counter_before": cnt_before, "counter_after": cnt_after, "predicate_ms": p_wait},
            }

        step_doc = {
            "schema": STEP_SCHEMA,
            "index": i,
            "name": step_name,
            "action": action,
            "predicate": predicate,
            "result": result,
            "timings": timings,
            "captures": captures,
            "trees": trees,
        }
        (s_dir / "step.json").write_text(json.dumps(step_doc, indent=2) + "\n")

        step_summaries.append({
            "index": i,
            "dir": f"steps/{i:03d}",
            "name": step_name,
            "kind": step_kind,
            "status": step_status,
            "duration_ms": duration_ms,
            "display_id": 98,
        })

    is_run_fail = fail_at is not None and fail_at < steps
    steps_ok = sum(1 for s in step_summaries if s["status"] == "ok")
    total_duration_ms = round((cur_time - created_at) * 1000)

    run_doc = {
        "schema": RUN_SCHEMA,
        "run_id": run_id,
        "kind": kind,
        "created_at": _iso(created_at),
        "finished_at": _iso(cur_time),
        "device": {"model": "Pixel 10 Pro Fold", "android_release": "17", "api_level": 37},
        "trail": {
            "name": "fixture-counter",
            "path": "trail.json",
            "sha256": trail_sha,
            "step_count": steps,
            "source_run_id": f"synthetic-record-{run_id}" if kind == "replay" else None,
        },
        "displays": [
            {"sf_id": "1", "unique_id": "local:1", "name": "Inner Display", "kind": "physical",
             "logical_id": 0, "width": 120, "height": 200, "state": "ON", "owner": None,
             "status_bar_px": 20, "role": "human"},
            {"sf_id": "2", "unique_id": "virtual:com.android.shell,2000,Cua agent,90", "name": "Cua agent",
             "kind": "virtual", "logical_id": 98, "width": size[0], "height": size[1], "state": "ON",
             "owner": "com.android.shell", "status_bar_px": 0, "role": "agent"},
            {"sf_id": "3", "unique_id": "local:3", "name": "Outer Display", "kind": "physical",
             "logical_id": 3, "width": 60, "height": 120, "state": "OFF", "owner": None,
             "status_bar_px": 10, "role": "human"},
        ],
        "session": {
            "session_id": "synth-session-001",
            "label": "phone-lab demo",
            "display_id": 98,
            "package": "ai.cua.fixture.notes",
            "target_id": "synth-target",
            "state": "active",
            "lease_remaining_ms": 60000,
            "lease_checked_at": created_at,
            "last_action": {"kind": "tap", "at": created_at, "result": "ok", "detail": {}},
            "owner": "phonelab synth",
            "updated_at": created_at,
        },
        "steps": step_summaries,
        "result": {
            "status": "fail" if is_run_fail else "pass",
            "steps_total": steps,
            "steps_ok": steps_ok,
            "steps_failed": 1 if is_run_fail else 0,
            "message": f"step {fail_at} failed" if is_run_fail else None,
        },
        "timings": {
            "started_at": created_at,
            "finished_at": cur_time,
            "total_ms": total_duration_ms,
        },
        "tool": {"name": "phonelab", "version": "0.1", "command": kind},
    }

    (run_dir / "run.json").write_text(json.dumps(run_doc, indent=2) + "\n")
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic trace runs.")
    parser.add_argument("--runs-dir", required=True, help="destination directory for runs")
    parser.add_argument("--runs", type=int, default=2, help="number of runs to generate")
    parser.add_argument("--steps", type=int, default=5, help="number of steps per run")
    args = parser.parse_args(argv)

    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    now = time.time()

    for i in range(args.runs):
        letter = chr(ord("a") + i)
        run_id = f"synthetic-{letter}-{ts}"
        drift = 12 if i == 1 else 0
        fail = 3 if (i == 1 and args.steps > 3) else None
        created = now - (args.runs - 1 - i) * 60.0
        p = make_run(runs_dir, run_id, steps=args.steps, seed=i, drift_px=drift,
                     fail_at=fail, created_at=created)
        print(p)

    return 0


if __name__ == "__main__":
    sys.exit(main())
