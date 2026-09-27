"""Tests for RunWriter trace output and Runner record/replay on FakeBackend (no device)."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from phonelab.cua import CuaError
from phonelab.displays import Display
from phonelab.refs import assign_refs
from phonelab.replay import AdbBackend, Runner, capture_all, record, replay, to_tree_doc
from phonelab.sessions import Registry
from phonelab.trace import list_runs, load_run, load_step
from phonelab.trails import Trail, load_trail, save_trail

FIXTURE_TRAIL_PATH = Path(__file__).parent / "fixtures" / "trail_fixture_five.json"
FIXTURE_TREE_PATH = Path(__file__).parent / "fixtures" / "tree_cua_fixture.json"


def _make_tiny_png(w: int = 100, h: int = 100) -> bytes:
    img = Image.new("RGB", (w, h), (40, 50, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


TINY_PNG = _make_tiny_png()


class FakeAdb:
    serial = "FAKESERIAL123"
    model = "Pixel 10 Pro Fold"
    tag = "pixel-10-pro-fold"

    def redact(self, text: str) -> str:
        return text.replace("FAKESERIAL123", "<serial>")

    def props(self) -> dict:
        return {"android_release": "17", "api_level": 37}


class FakeDriver:
    def __init__(self, backend: "FakeBackend", report_lease_20s: bool = False) -> None:
        self.backend = backend
        self.report_lease_20s = report_lease_20s
        self.calls: list[tuple[str, ...]] = []
        self.first_tap_refused = False

    def create(self, allow_apps: list[str], label: str) -> dict:
        self.calls.append(("create", allow_apps, label))
        lease = 20000 if self.report_lease_20s else 60000
        return {
            "status": "ok",
            "exit_code": 0,
            "data": {
                "session_id": "fake-sid-1",
                "display_id": 98,
                "lease_remaining_ms": lease,
                "label": label,
            },
        }

    def launch(self, sid: str, package: str) -> dict:
        self.calls.append(("launch", sid, package))
        return {
            "status": "ok",
            "exit_code": 0,
            "data": {"target_id": "fake-target-1", "package": package},
        }

    def snapshot(self, sid: str, target_id: str) -> dict:
        self.calls.append(("snapshot", sid, target_id))
        return {
            "status": "ok",
            "exit_code": 0,
            "data": {"snapshot_id": "snap-123", "frame_age_ms": 10},
        }

    def tap(self, sid: str, snapshot_id: str, x: int, y: int) -> dict:
        self.calls.append(("tap", sid, snapshot_id, x, y))
        if not self.first_tap_refused:
            self.first_tap_refused = True
            raise CuaError("refused", "frame_stale")

        if (x, y) == (540, 263):
            self.backend.counter += 1
        return {"status": "ok", "exit_code": 0, "data": {}}

    def renew(self, sid: str) -> dict:
        self.calls.append(("renew", sid))
        return {"status": "ok", "exit_code": 0, "data": {"lease_remaining_ms": 60000}}

    def stop(self, sid: str) -> dict:
        self.calls.append(("stop", sid))
        return {"status": "ok", "exit_code": 0, "data": {"state": "stopped"}}


class FakeBackend:
    def __init__(self, report_lease_20s: bool = False) -> None:
        self.adb = FakeAdb()
        self.driver = FakeDriver(self, report_lease_20s=report_lease_20s)
        self.counter = 0
        self._tree_fixture = json.loads(FIXTURE_TREE_PATH.read_text())
        self.started = False
        self.stopped = False

    def inventory(self) -> list[Display]:
        return [
            Display(
                sf_id="1",
                unique_id="local:1",
                name="Inner Display",
                kind="physical",
                logical_id=0,
                width=100,
                height=100,
                state="ON",
                owner=None,
                status_bar_px=20,
                role="human",
            ),
            Display(
                sf_id="2",
                unique_id="virtual:com.android.shell,2000,Cua agent,98",
                name="Cua agent",
                kind="virtual",
                logical_id=98,
                width=100,
                height=100,
                state="ON",
                owner="com.android.shell",
                status_bar_px=0,
                role="agent",
            ),
        ]

    def screencap(self, sf_id: str) -> bytes | None:
        return TINY_PNG

    def tree(self, logical_id: int) -> dict:
        t = copy.deepcopy(self._tree_fixture)
        t["display_id"] = logical_id
        # Update node 4 (counter text)
        for n in t.get("nodes", []):
            if n.get("id") == "ai.cua.fixture.notes:id/counter":
                n["text"] = f"Count: {self.counter}"
        assign_refs(t)
        return t

    def act(self, logical_id: int, node_index: int, action: str) -> dict:
        return {"ok": True}

    def shell(self, *args: str) -> str:
        if len(args) >= 3 and args[0] == "am" and args[1] == "force-stop":
            self.counter = 0
        return ""

    def fixture_state(self) -> dict | None:
        return {
            "counter": self.counter,
            "display_id": 98,
            "controls": {"increment": {"x": 540, "y": 263}},
        }

    def props(self) -> dict:
        p = self.adb.props()
        return {
            "model": self.adb.model,
            "android_release": p["android_release"],
            "api_level": p["api_level"],
        }

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class ReplayUnitTests(unittest.TestCase):
    def test_1_replay_of_fixture_trail_passes(self):
        backend = FakeBackend(report_lease_20s=True)
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            ret = replay(backend, registry, runs_dir, FIXTURE_TRAIL_PATH, times=1)
            self.assertEqual(ret, 0)

            runs = list_runs(runs_dir)
            self.assertEqual(len(runs), 1)
            run_id = runs[0]["run_id"]
            run_dir = runs_dir / run_id

            run_doc = load_run(runs_dir, run_id)
            self.assertEqual(run_doc["result"]["status"], "pass")
            self.assertEqual(len(run_doc["steps"]), 5)

            # Check 5 step dirs
            for i in range(5):
                step_dir = run_dir / "steps" / f"{i:03d}"
                self.assertTrue(step_dir.is_dir())

                step_doc = load_step(runs_dir, run_id, i)
                self.assertEqual(step_doc["index"], i)
                self.assertEqual(step_doc["result"]["status"], "ok")

                # Validate captures
                for phase in ("before", "after"):
                    panels = step_doc["captures"][phase]
                    self.assertEqual(len(panels), 2)
                    for panel in panels:
                        img_name = panel.get("image")
                        self.assertIsNotNone(img_name)
                        img_path = step_dir / img_name
                        self.assertTrue(img_path.is_file())
                        actual_sha = hashlib.sha256(img_path.read_bytes()).hexdigest()
                        self.assertEqual(panel["png_sha256"], actual_sha)

                        if panel["role"] == "agent":
                            self.assertEqual(img_name, f"{phase}-logical-98.png")
                        elif panel["role"] == "human":
                            # height 100 - 20 = 80
                            self.assertEqual(panel["height"], 80)
                            self.assertEqual(panel["cropped_status_bar_px"], 20)

                # Validate trees
                for phase in ("before", "after"):
                    tree_file = step_doc["trees"][phase]["logical-98"]
                    self.assertTrue((step_dir / tree_file).is_file())

            # Step 1 specific checks: frame_stale_retries == 1, x,y == (540, 263)
            step1 = load_step(runs_dir, run_id, 1)
            self.assertEqual(step1["result"]["detail"]["frame_stale_retries"], 1)
            self.assertEqual(step1["action"]["detail"]["x"], 540)
            self.assertEqual(step1["action"]["detail"]["y"], 263)

            # Check lease was renewed
            renew_calls = [c for c in backend.driver.calls if c[0] == "renew"]
            self.assertGreaterEqual(len(renew_calls), 1)

            # Check session stopped
            stop_calls = [c for c in backend.driver.calls if c[0] == "stop"]
            self.assertEqual(len(stop_calls), 1)

            # Check registry record
            recs = registry.load_all()
            self.assertEqual(len(recs), 1)
            rec = recs[0]
            self.assertEqual(rec.state, "stopped")
            self.assertEqual(rec.owner, "phonelab trail")

    def test_2_absent_ref_fails_and_skips_unless_continue_on_fail(self):
        backend1 = FakeBackend()
        backend2 = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir1 = Path(tmp) / "runs1"
            registry1 = Registry(runs_dir1, "pixel-10-pro-fold")

            # Create trail where step 1 names absent ref
            trail = load_trail(FIXTURE_TRAIL_PATH)
            trail.steps[1].action["ref"] = "nonexistent_ref"
            bad_trail_path = Path(tmp) / "bad_trail.json"
            save_trail(trail, bad_trail_path)

            # 1. Default stop_on_fail=True: fails at step 1, steps 2-4 skipped
            ret1 = replay(backend1, registry1, runs_dir1, bad_trail_path, stop_on_fail=True)
            self.assertEqual(ret1, 1)

            runs1 = list_runs(runs_dir1)
            self.assertEqual(len(runs1), 1)
            run_id1 = runs1[0]["run_id"]
            run_doc1 = load_run(runs_dir1, run_id1)
            self.assertEqual(run_doc1["result"]["status"], "fail")

            step1 = load_step(runs_dir1, run_id1, 1)
            self.assertEqual(step1["result"]["status"], "fail")
            self.assertIn("nonexistent_ref", step1["result"]["message"])

            for i in (2, 3, 4):
                s = load_step(runs_dir1, run_id1, i)
                self.assertEqual(s["result"]["status"], "skipped")

            # 2. continue-on-fail (stop_on_fail=False): runs remaining steps
            runs_dir2 = Path(tmp) / "runs2"
            registry2 = Registry(runs_dir2, "pixel-10-pro-fold")

            ret2 = replay(backend2, registry2, runs_dir2, bad_trail_path, stop_on_fail=False)
            self.assertEqual(ret2, 1)

            runs2 = list_runs(runs_dir2)
            self.assertEqual(len(runs2), 1)
            run_id2 = runs2[0]["run_id"]

            step1_2 = load_step(runs_dir2, run_id2, 1)
            self.assertEqual(step1_2["result"]["status"], "fail")

            # steps 2 and 3 should run (not skipped)
            step2_2 = load_step(runs_dir2, run_id2, 2)
            self.assertNotEqual(step2_2["result"]["status"], "skipped")

    def test_3_record_derives_predicates_and_matches_fixture(self):
        script = """launch ai.cua.fixture.notes
        tap e7f67h
        tap e7f67h
        tap e7f67h
        wait_for text_present "Count: 3"
        """
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            trails_dir = Path(tmp) / "trails"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            ret = record(backend, registry, runs_dir, trails_dir, "fixture-five", script)
            self.assertEqual(ret, 0)

            out_trail_path = trails_dir / "fixture-five.json"
            self.assertTrue(out_trail_path.is_file())

            recorded_trail = load_trail(out_trail_path)
            fixture_trail = load_trail(FIXTURE_TRAIL_PATH)

            rec_dict = recorded_trail.to_json()
            fix_dict = fixture_trail.to_json()

            # Ignore created_at difference
            rec_dict["created_at"] = fix_dict["created_at"]
            self.assertEqual(rec_dict, fix_dict)

    def test_4_times_runs_in_one_session_with_multiple_launches(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            ret = replay(backend, registry, runs_dir, FIXTURE_TRAIL_PATH, times=3)
            self.assertEqual(ret, 0)

            runs = list_runs(runs_dir)
            self.assertEqual(len(runs), 3)

            run_ids = {r["run_id"] for r in runs}
            self.assertEqual(len(run_ids), 3)

            create_calls = [c for c in backend.driver.calls if c[0] == "create"]
            self.assertEqual(len(create_calls), 3)

            stop_calls = [c for c in backend.driver.calls if c[0] == "stop"]
            self.assertEqual(len(stop_calls), 3)

            launch_calls = [c for c in backend.driver.calls if c[0] == "launch"]
            self.assertEqual(len(launch_calls), 3)

    def test_5_agent_only_skips_human_display(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            ret = replay(backend, registry, runs_dir, FIXTURE_TRAIL_PATH, times=1, capture_human=False)
            self.assertEqual(ret, 0)

            runs = list_runs(runs_dir)
            run_id = runs[0]["run_id"]
            run_dir = runs_dir / run_id
            step0 = load_step(runs_dir, run_id, 0)

            human_panel = [p for p in step0["captures"]["before"] if p["role"] == "human"][0]
            self.assertIsNone(human_panel["image"])
            self.assertEqual(human_panel["capture_error"], "skipped")

            step_dir = run_dir / "steps" / "000"
            human_pngs = list(step_dir.glob("*logical-0.png"))
            self.assertEqual(len(human_pngs), 0)

    def test_6_list_runs_newest_first(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            # Run 2 replays with different run ids
            replay(backend, registry, runs_dir, FIXTURE_TRAIL_PATH, times=1)
            time.sleep(1.05)  # ensure distinct timestamp
            replay(backend, registry, runs_dir, FIXTURE_TRAIL_PATH, times=1)

            runs = list_runs(runs_dir)
            self.assertEqual(len(runs), 2)
            self.assertGreater(runs[0]["created_at"], runs[1]["created_at"])


if __name__ == "__main__":
    unittest.main()
