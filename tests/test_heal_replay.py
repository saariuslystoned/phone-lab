"""Tests for self-heal integration in replay (slice 5)."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from phonelab.refs import assign_refs
from phonelab.replay import replay
from phonelab.sessions import Registry
from phonelab.trace import list_runs, load_run, load_step
from phonelab.trails import Trail, load_trail, save_trail
from tests.test_replay import FIXTURE_TRAIL_PATH, FakeBackend

RECORDED_INCREMENT_NODE = {
    "i": 3,
    "class": "android.widget.Button",
    "text": "INCREMENT",
    "desc": None,
    "id": "ai.cua.fixture.notes:id/increment",
    "bounds": [24, 215, 1056, 311],
    "clickable": True,
    "long_clickable": False,
    "editable": False,
    "checkable": False,
    "focusable": True,
    "visible": True,
}


class ShiftedBackend(FakeBackend):
    def __init__(self, dx: int = 0, dy: int = 0, **kwargs) -> None:
        super().__init__(**kwargs)
        self.dx = dx
        self.dy = dy

    def tree(self, logical_id: int) -> dict:
        t = copy.deepcopy(self._tree_fixture)
        t["display_id"] = logical_id
        for n in t.get("nodes", []):
            if n.get("id") == "ai.cua.fixture.notes:id/counter":
                n["text"] = f"Count: {self.counter}"
            elif n.get("id") == "ai.cua.fixture.notes:id/increment":
                b = n.get("bounds", [0, 0, 0, 0])
                n["bounds"] = [b[0] + self.dx, b[1] + self.dy, b[2] + self.dx, b[3] + self.dy]
        assign_refs(t)
        return t


class FailingTreeBackend(FakeBackend):
    def __init__(self, fail_after_calls: int = 2, **kwargs) -> None:
        super().__init__(**kwargs)
        self.fail_after_calls = fail_after_calls
        self.tree_calls = 0

    def tree(self, logical_id: int) -> dict:
        self.tree_calls += 1
        if self.tree_calls > self.fail_after_calls:
            return {"ok": False, "error": "no windows", "display_id": logical_id, "nodes": []}
        return super().tree(logical_id)


class HealReplayTests(unittest.TestCase):
    def _make_trail_with_recorded(self, tmp: Path) -> Path:
        trail = load_trail(FIXTURE_TRAIL_PATH)
        # Keep launch and first tap step
        trail.steps = trail.steps[:2]
        trail.steps[1].action["recorded"] = dict(RECORDED_INCREMENT_NODE)
        path = tmp / "trail_recorded.json"
        save_trail(trail, path)
        return path

    def test_healed(self) -> None:
        backend = ShiftedBackend(dx=60, dy=0)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_dir = tmp_path / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            trail_path = self._make_trail_with_recorded(tmp_path)

            ret = replay(backend, registry, runs_dir, trail_path)
            self.assertEqual(ret, 0)

            runs = list_runs(runs_dir)
            self.assertEqual(len(runs), 1)
            run_id = runs[0]["run_id"]

            run_doc = load_run(runs_dir, run_id)
            self.assertEqual(run_doc["result"]["status"], "pass")
            self.assertIn("heal", run_doc)
            self.assertEqual(run_doc["heal"], {"max_distance_px": 120})

            step1 = load_step(runs_dir, run_id, 1)
            self.assertEqual(step1["result"]["status"], "healed")
            self.assertIn("healed: moved 60 px", step1["result"]["message"])

            # Verify trees.heal
            self.assertIn("heal", step1["trees"])
            rec_tree_file = step1["trees"]["heal"]["recorded"]
            cur_tree_file = step1["trees"]["heal"]["current"]
            step_dir = runs_dir / run_id / "steps" / "001"
            self.assertTrue((step_dir / rec_tree_file).is_file())
            self.assertTrue((step_dir / cur_tree_file).is_file())

            # Verify action.detail and action.ref
            self.assertEqual(step1["action"]["ref"], "e7f67h")
            new_ref = step1["action"]["detail"]["ref_used"]
            self.assertNotEqual(new_ref, "e7f67h")
            self.assertEqual(step1["action"]["detail"]["x"], 600)
            self.assertEqual(step1["action"]["detail"]["y"], 263)

            # Verify heal result detail
            heal_detail = step1["result"]["detail"]["heal"]
            self.assertEqual(heal_detail["status"], "healed")
            self.assertEqual(heal_detail["ref"], new_ref)
            self.assertEqual(heal_detail["note"]["distance_px"], 60)

            # Counter actually incremented
            self.assertEqual(backend.counter, 1)

    def test_out_of_bound(self) -> None:
        backend = ShiftedBackend(dx=400, dy=0)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_dir = tmp_path / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            trail_path = self._make_trail_with_recorded(tmp_path)

            ret = replay(backend, registry, runs_dir, trail_path)
            self.assertEqual(ret, 1)

            runs = list_runs(runs_dir)
            run_id = runs[0]["run_id"]
            run_doc = load_run(runs_dir, run_id)
            self.assertEqual(run_doc["result"]["status"], "fail")

            step1 = load_step(runs_dir, run_id, 1)
            self.assertEqual(step1["result"]["status"], "fail")
            msg = step1["result"]["message"]
            self.assertIn("out_of_bound", msg)
            rec_tree_file = step1["trees"]["heal"]["recorded"]
            cur_tree_file = step1["trees"]["heal"]["current"]
            self.assertIn(rec_tree_file, msg)
            self.assertIn(cur_tree_file, msg)

            heal_detail = step1["result"]["detail"]["heal"]
            self.assertEqual(heal_detail["status"], "failed")
            self.assertEqual(heal_detail["note"]["reason"], "out_of_bound")

            # No tap executed, counter untouched
            self.assertEqual(backend.counter, 0)

    def test_disabled(self) -> None:
        backend = ShiftedBackend(dx=60, dy=0)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_dir = tmp_path / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            trail_path = self._make_trail_with_recorded(tmp_path)

            ret = replay(backend, registry, runs_dir, trail_path, max_heal_px=0)
            self.assertEqual(ret, 1)

            runs = list_runs(runs_dir)
            run_id = runs[0]["run_id"]
            run_doc = load_run(runs_dir, run_id)
            self.assertEqual(run_doc["heal"], {"max_distance_px": 0})

            step1 = load_step(runs_dir, run_id, 1)
            self.assertEqual(step1["result"]["status"], "fail")
            heal_detail = step1["result"]["detail"]["heal"]
            self.assertEqual(heal_detail["status"], "failed")
            self.assertEqual(heal_detail["note"]["reason"], "out_of_bound")
            self.assertEqual(backend.counter, 0)

    def test_no_recorded_node(self) -> None:
        backend = ShiftedBackend(dx=60, dy=0)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_dir = tmp_path / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")

            # Load trail fixture which has no action["recorded"]
            trail = load_trail(FIXTURE_TRAIL_PATH)
            trail.steps = trail.steps[:2]
            trail_path = tmp_path / "trail_legacy.json"
            save_trail(trail, trail_path)

            ret = replay(backend, registry, runs_dir, trail_path)
            self.assertEqual(ret, 1)

            runs = list_runs(runs_dir)
            run_id = runs[0]["run_id"]

            step1 = load_step(runs_dir, run_id, 1)
            self.assertEqual(step1["result"]["status"], "fail")
            self.assertIn("trail has no recorded node, re-record to enable healing", step1["result"]["message"])
            self.assertNotIn("heal", step1["trees"])
            self.assertNotIn("heal", step1["result"]["detail"])
            self.assertEqual(backend.counter, 0)

    def test_tree_not_ok_fails_step(self) -> None:
        backend = FailingTreeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runs_dir = tmp_path / "runs"
            registry = Registry(runs_dir, "pixel-10-pro-fold")
            trail_path = self._make_trail_with_recorded(tmp_path)

            ret = replay(backend, registry, runs_dir, trail_path)
            self.assertEqual(ret, 1)

            runs = list_runs(runs_dir)
            self.assertEqual(len(runs), 1)
            run_id = runs[0]["run_id"]

            run_doc = load_run(runs_dir, run_id)
            self.assertEqual(run_doc["result"]["status"], "fail")
            self.assertNotEqual(run_doc["result"]["status"], "aborted")

            step1 = load_step(runs_dir, run_id, 1)
            self.assertEqual(step1["result"]["status"], "fail")
            self.assertIn("heal skipped", step1["result"]["message"])
            self.assertNotIn("heal", step1["result"]["detail"])
            self.assertNotIn("heal", step1["trees"])
            self.assertEqual(backend.counter, 0)


if __name__ == "__main__":
    unittest.main()
