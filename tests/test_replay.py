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
from phonelab.replay import AdbBackend, RunWriter, Runner, _make_skipped_step, capture_all, record, replay, to_tree_doc
from phonelab.sessions import Registry
from phonelab.trace import list_runs, load_run, load_step
from phonelab.trails import Step, Trail, load_trail, save_trail

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
    def __init__(self, backend: "FakeBackend", report_lease_20s: bool = False, display_id: int = 98) -> None:
        self.backend = backend
        self.report_lease_20s = report_lease_20s
        self.display_id = display_id
        self.calls: list[tuple[str, ...]] = []
        self.first_tap_refused = False

    def create(self, allow_apps: list[str], label: str, *, size: str | None = None, density: int | None = None) -> dict:
        self.calls.append(("create", allow_apps, label))
        self.create_kwargs = {"size": size, "density": density}
        lease = 20000 if self.report_lease_20s else 60000
        return {
            "status": "ok",
            "exit_code": 0,
            "data": {
                "session_id": "fake-sid-1",
                "display_id": self.display_id,
                "lease_remaining_ms": lease,
                "label": label,
            },
        }

    def launch(self, sid: str, package: str, activity: str | None = None) -> dict:
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

        expected_x = 540 + getattr(self.backend, "dx", 0)
        expected_y = 263 + getattr(self.backend, "dy", 0)
        if (x, y) == (expected_x, expected_y):
            self.backend.counter += 1
        return {"status": "ok", "exit_code": 0, "data": {}}

    def renew(self, sid: str) -> dict:
        self.calls.append(("renew", sid))
        return {"status": "ok", "exit_code": 0, "data": {"lease_remaining_ms": 60000}}

    def stop(self, sid: str) -> dict:
        self.calls.append(("stop", sid))
        return {"status": "ok", "exit_code": 0, "data": {"state": "stopped"}}


class FakeBackend:
    def __init__(self, report_lease_20s: bool = False, display_id: int = 98) -> None:
        self.adb = FakeAdb()
        self.display_id = display_id
        self.driver = FakeDriver(self, report_lease_20s=report_lease_20s, display_id=display_id)
        self.counter = 0
        self._tree_fixture = json.loads(FIXTURE_TREE_PATH.read_text())
        self.started = False
        self.stopped = False
        self.stop_count = 0
        self.inventory_calls = 0
        self.raise_inventory_after_open_session = False
        self.shell_calls: list[tuple[str, ...]] = []
        self.added_apps: list[str] = []

    def inventory(self) -> list[Display]:
        self.inventory_calls += 1
        if self.raise_inventory_after_open_session and any(c[0] == "create" for c in self.driver.calls):
            raise RuntimeError("inventory failed after open_session")
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
                unique_id=f"virtual:com.android.shell,2000,Cua agent,{self.display_id}",
                name="Cua agent",
                kind="virtual",
                logical_id=self.display_id,
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

    def shell(self, *args: str, timeout: float = 15) -> str:
        self.shell_calls.append(tuple(args))
        if len(args) >= 3 and args[0] == "am" and args[1] == "force-stop":
            self.counter = 0
        return ""

    def add_apps(self, pkgs: Any) -> None:
        self.added_apps.extend(pkgs)

    def fixture_state(self) -> dict | None:
        return {
            "counter": self.counter,
            "display_id": self.display_id,
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
        self.stop_count += 1


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
                self.assertEqual(s["action"]["display_id"], 98)

            # Assert skipped steps carry the real session display_id (e.g. 77) without a literal fallback
            backend_77 = FakeBackend(display_id=77)
            runs_dir_77 = Path(tmp) / "runs_77"
            registry_77 = Registry(runs_dir_77, "pixel-10-pro-fold")
            ret_77 = replay(backend_77, registry_77, runs_dir_77, bad_trail_path, stop_on_fail=True)
            self.assertEqual(ret_77, 1)
            run_id_77 = list_runs(runs_dir_77)[0]["run_id"]
            for i in (2, 3, 4):
                s_77 = load_step(runs_dir_77, run_id_77, i)
                self.assertEqual(s_77["result"]["status"], "skipped")
                self.assertEqual(s_77["action"]["display_id"], 77)

            # Assert _make_skipped_step accepts display_id=None
            s_none = _make_skipped_step(0, trail.steps[0], None)
            self.assertIsNone(s_none["action"]["display_id"])

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
            for s in rec_dict["steps"]:
                if s["action"]["kind"] == "tap":
                    self.assertIn("recorded", s["action"])
                    self.assertEqual(s["action"]["recorded"]["id"], "ai.cua.fixture.notes:id/increment")
                    del s["action"]["recorded"]
            self.assertEqual(rec_dict, fix_dict)

    def test_journey_sentence_names_carry_into_record_and_replay_runs(self):
        sentences = [
            "Launch the Synthetic Notes Fixture from a fresh start.",
            "Tap the INCREMENT button.",
            "Verify the counter text reads \"Count: 1\" (don't scroll).",
        ]
        script = (
            f"name {sentences[0]}\nlaunch ai.cua.fixture.notes\n"
            f"name {sentences[1]}\ntap e7f67h\n"
            f"name {sentences[2]}\nwait_for text_present \"Count: 1\"\n"
        )
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            trails_dir = Path(tmp) / "trails"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            self.assertEqual(record(backend, registry, runs_dir, trails_dir, "journey", script), 0)
            trail_path = trails_dir / "journey.json"
            self.assertEqual([s.name for s in load_trail(trail_path).steps], sentences)
            self.assertEqual(replay(backend, registry, runs_dir, trail_path, times=1), 0)
            runs = list_runs(runs_dir)
            self.assertEqual(len(runs), 2)
            for r in runs:
                run_doc = json.loads((runs_dir / r["run_id"] / "run.json").read_text())
                self.assertEqual([s["name"] for s in run_doc["steps"]], sentences)
                for i, sentence in enumerate(sentences):
                    self.assertEqual(load_step(runs_dir, r["run_id"], i)["name"], sentence)

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

    def test_record_cleans_up_when_inventory_fails_after_open_session(self):
        backend = FakeBackend()
        backend.raise_inventory_after_open_session = True
        script = "launch ai.cua.fixture.notes\n"

        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            trails_dir = Path(tmp) / "trails"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            with self.assertRaises(RuntimeError):
                record(backend, registry, runs_dir, trails_dir, "fail-run", script)

            # Assert fake driver recorded a "stop" call for the created session
            stop_calls = [c for c in backend.driver.calls if c[0] == "stop"]
            self.assertEqual(len(stop_calls), 1)
            self.assertEqual(stop_calls[0][1], "fake-sid-1")

            # Assert backend.stop() was called
            self.assertTrue(backend.stopped)
            self.assertGreaterEqual(backend.stop_count, 1)

            # Assert registry record is state "stopped"
            recs = registry.load_all()
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0].state, "stopped")



    def test_launch_with_extras_and_input_tap(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            runner = Runner(backend, registry, runs_dir)
            runner.open_session(["com.example.app"], "test-session")

            writer = RunWriter(
                runs_dir=runs_dir,
                run_id="run-launch-extras",
                kind="record",
                device=backend.props(),
                trail_path_src=None,
                trail_name="test-launch",
                step_count=2,
                source_run_id=None,
                displays=backend.inventory(),
                session=runner.session_record.to_json(),
                command="record",
                heal=None,
            )
            writer.begin()

            step1 = Step(
                name="launch with extras",
                display="agent",
                action={
                    "kind": "launch",
                    "package": "com.example.app",
                    "activity": "com.example.app.MainActivity",
                    "extras": {"flag": True, "num": 42, "text": "hello"},
                },
                predicate=None,
            )
            doc1, _ = runner.run_step(0, step1, writer, derive=False)
            self.assertEqual(doc1["result"]["status"], "ok")
            self.assertIsNone(runner.target_id)
            self.assertEqual(doc1["action"]["detail"]["via"], "am")
            self.assertEqual(doc1["action"]["detail"]["package"], "com.example.app")
            self.assertEqual(doc1["action"]["detail"]["activity"], "com.example.app.MainActivity")

            # Check am shell commands
            am_calls = [c for c in backend.shell_calls if c[0] == "am"]
            self.assertIn(("am", "force-stop", "com.example.app"), am_calls)
            expected_start = (
                "am", "start", "-W", "--display", "98", "-n",
                "com.example.app/com.example.app.MainActivity",
                "--ez", "flag", "true",
                "--ei", "num", "42",
                "--es", "text", "hello",
            )
            self.assertIn(expected_start, am_calls)

            # Now step 2: tap when target_id is None -> input tap
            step2 = Step(
                name="tap increment",
                display="agent",
                action={"kind": "tap", "ref": "e7f67h"},
                predicate=None,
            )
            doc2, _ = runner.run_step(1, step2, writer, derive=False)
            self.assertEqual(doc2["result"]["status"], "ok")
            self.assertEqual(doc2["action"]["detail"]["via"], "input")
            input_calls = [c for c in backend.shell_calls if c[0] == "input" and len(c) >= 4 and c[3] == "tap"]
            self.assertEqual(len(input_calls), 1)
            self.assertEqual(input_calls[0][:4], ("input", "-d", "98", "tap"))
            runner.close_session()

    def test_resize_action_and_cleanup(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            runner = Runner(backend, registry, runs_dir)
            runner.open_session(["ai.cua.fixture.notes"], "test-resize")

            writer = RunWriter(
                runs_dir=runs_dir,
                run_id="run-resize",
                kind="record",
                device=backend.props(),
                trail_path_src=None,
                trail_name="test-resize",
                step_count=3,
                source_run_id=None,
                displays=backend.inventory(),
                session=runner.session_record.to_json(),
                command="record",
                heal=None,
            )
            writer.begin()

            # 1. Resize to WxH with density
            step1 = Step(
                name="resize phone",
                display="agent",
                action={"kind": "resize", "size": "1080x1920", "density": 320},
                predicate=None,
            )
            doc1, _ = runner.run_step(0, step1, writer, derive=False)
            self.assertEqual(doc1["result"]["status"], "ok")
            self.assertIn(98, runner.resized_displays)
            self.assertIn(("wm", "size", "1080x1920", "-d", "98"), backend.shell_calls)
            self.assertIn(("wm", "density", "320", "-d", "98"), backend.shell_calls)

            # 2. Resize reset
            step2 = Step(
                name="reset display",
                display="agent",
                action={"kind": "resize", "size": "reset"},
                predicate=None,
            )
            doc2, _ = runner.run_step(1, step2, writer, derive=False)
            self.assertEqual(doc2["result"]["status"], "ok")
            self.assertIn(("wm", "size", "reset", "-d", "98"), backend.shell_calls)
            self.assertIn(("wm", "density", "reset", "-d", "98"), backend.shell_calls)

            # 3. Resize again, then close_session() to test cleanup
            step3 = Step(
                name="resize custom",
                display="agent",
                action={"kind": "resize", "size": "720x1280"},
                predicate=None,
            )
            doc3, _ = runner.run_step(2, step3, writer, derive=False)
            self.assertEqual(doc3["result"]["status"], "ok")

            backend.shell_calls.clear()
            runner.close_session()

            # Verify wm size/density reset were called in close_session before stop
            self.assertIn(("wm", "size", "reset", "-d", "98"), backend.shell_calls)
            self.assertIn(("wm", "density", "reset", "-d", "98"), backend.shell_calls)
            self.assertEqual(len(runner.resized_displays), 0)

    def test_record_resolves_tap_label_and_replay_uses_ref(self):
        backend = FakeBackend()
        script_pass = (
            "launch ai.cua.fixture.notes\n"
            "tap label:\"INCREMENT\"\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            trails_dir = Path(tmp) / "trails"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            ret = record(backend, registry, runs_dir, trails_dir, "label-test", script_pass)
            self.assertEqual(ret, 0)

            trail_path = trails_dir / "label-test.json"
            self.assertTrue(trail_path.is_file())
            trail_data = json.loads(trail_path.read_text())
            tap_step = trail_data["steps"][1]
            # Verify ref was resolved and recorded/hint/label populated
            self.assertEqual(tap_step["action"]["label"], "INCREMENT")
            self.assertEqual(tap_step["action"]["ref"], "e7f67h")
            self.assertIsNotNone(tap_step["action"].get("recorded"))
            self.assertEqual(tap_step["action"].get("hint", {}).get("label"), "INCREMENT")

            # Replay with the recorded trail
            ret_rep = replay(backend, registry, runs_dir, trail_path)
            self.assertEqual(ret_rep, 0)

            # Test ambiguous or unresolvable label fails record
            script_fail = (
                "launch ai.cua.fixture.notes\n"
                "tap label:\"Nonexistent Button\"\n"
            )
            ret_fail = record(backend, registry, runs_dir, trails_dir, "label-fail", script_fail)
            self.assertEqual(ret_fail, 1)

    def test_record_other_app_derives_no_fixture_predicates(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            trails_dir = Path(tmp) / "trails"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            script = "launch com.example.other\ntap label:\"INCREMENT\"\n"
            self.assertEqual(record(backend, registry, runs_dir, trails_dir, "other-app", script), 0)
            data = json.loads((trails_dir / "other-app.json").read_text())
            self.assertEqual(data["session"]["allow_apps"], ["com.example.other"])
            for step in data["steps"]:
                self.assertNotEqual((step.get("predicate") or {}).get("kind"), "fixture_counter")

    def test_label_step_re_resolves_when_ref_is_gone(self):
        from phonelab.trace import list_runs, load_step
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            trails_dir = Path(tmp) / "trails"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            self.assertEqual(record(backend, registry, runs_dir, trails_dir, "relabel",
                                    "launch ai.cua.fixture.notes\ntap label:\"INCREMENT\"\n"), 0)
            trail_path = trails_dir / "relabel.json"
            data = json.loads(trail_path.read_text())
            # The recorded node moved far and lost its own label (a Compose field whose label is a child).
            data["steps"][1]["action"]["ref"] = "zzzzzz"
            data["steps"][1]["action"]["recorded"]["text"] = ""
            trail_path.write_text(json.dumps(data))
            replay_runs = Path(tmp) / "replay-runs"
            self.assertEqual(replay(backend, registry, replay_runs, trail_path), 0)
            run_id = list_runs(replay_runs)[0]["run_id"]
            step = load_step(replay_runs, run_id, 1)
            self.assertEqual(step["result"]["status"], "healed")
            self.assertIn("re-resolved by label 'INCREMENT' to e7f67h", step["result"]["message"])
            self.assertEqual(step["result"]["detail"]["heal"]["note"]["reason"], "label")
            self.assertEqual(step["action"]["detail"]["ref_used"], "e7f67h")

    def test_backend_add_apps_and_cli_app(self):
        from phonelab.__main__ import build_parser
        parser = build_parser()

        args = parser.parse_args(["tree", "0", "--app", "pkg.a", "--app", "pkg.b"])
        self.assertEqual(args.app, ["pkg.a", "pkg.b"])

        args = parser.parse_args(["serve", "--app", "pkg.c"])
        self.assertEqual(args.app, ["pkg.c"])

        args = parser.parse_args(["trail", "record", "-", "--name", "t", "--app", "pkg.d"])
        self.assertEqual(args.app, ["pkg.d"])

        args = parser.parse_args(["trail", "replay", "t.json", "--app", "pkg.e"])
        self.assertEqual(args.app, ["pkg.e"])

        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp) / "runs"
            trails_dir = Path(tmp) / "trails"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            script = "launch ai.cua.fixture.notes\n"
            ret = record(backend, registry, runs_dir, trails_dir, "app-test", script, apps=["custom.app"])
            self.assertEqual(ret, 0)
            self.assertIn("custom.app", backend.added_apps)
            self.assertIn("ai.cua.fixture.notes", backend.added_apps)


if __name__ == "__main__":
    unittest.main()
