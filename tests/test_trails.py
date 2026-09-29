"""Tests for trail model, schemas, validation, and script parsing (no device)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phonelab.trails import (
    DEFAULT_TIMEOUT_MS,
    Step,
    Trail,
    TrailError,
    derive_predicate,
    load_trail,
    parse_script,
    save_trail,
    trail_sha256,
    validate_action,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "trail_fixture_five.json"


class TrailsUnitTests(unittest.TestCase):
    def test_round_trip_fixture(self):
        trail = load_trail(FIXTURE_PATH)
        self.assertEqual(trail.name, "fixture-five")
        self.assertEqual(len(trail.steps), 5)
        raw_data = json.loads(FIXTURE_PATH.read_text())
        self.assertEqual(trail.to_json(), raw_data)

        # Test save and reload
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "saved_trail.json"
            save_trail(trail, tmp_path)
            reloaded = load_trail(tmp_path)
            self.assertEqual(reloaded.to_json(), raw_data)
            self.assertEqual(trail_sha256(tmp_path), trail_sha256(FIXTURE_PATH))

    def test_validation_errors_name_step_index(self):
        raw_data = json.loads(FIXTURE_PATH.read_text())

        # Corrupt step 2 action
        data_bad_action = json.loads(json.dumps(raw_data))
        data_bad_action["steps"][2]["action"]["ref"] = ""
        with self.assertRaises(TrailError) as ctx:
            Trail.from_json(data_bad_action)
        self.assertIn("step 2", str(ctx.exception))

        # Corrupt step 0 display
        data_bad_disp = json.loads(json.dumps(raw_data))
        data_bad_disp["steps"][0]["display"] = "human"
        with self.assertRaises(TrailError) as ctx:
            Trail.from_json(data_bad_disp)
        self.assertIn("step 0", str(ctx.exception))

        # Corrupt step 4 predicate
        data_bad_pred = json.loads(json.dumps(raw_data))
        data_bad_pred["steps"][4]["predicate"]["text"] = ""
        with self.assertRaises(TrailError) as ctx:
            Trail.from_json(data_bad_pred)
        self.assertIn("step 4", str(ctx.exception))

    def test_parse_script_five_line_proof(self):
        script = """
        launch ai.cua.fixture.notes
        tap e7f67h
        tap e7f67h
        tap e7f67h
        wait_for text_present "Count: 3"
        """
        steps = parse_script(script)
        self.assertEqual(len(steps), 5)
        self.assertEqual([s["action"]["kind"] for s in steps], ["launch", "tap", "tap", "tap", "wait_for"])
        self.assertEqual(
            [s["name"] for s in steps],
            ["launch fixture", "tap e7f67h", "tap e7f67h", "tap e7f67h", "wait for Count: 3"],
        )
        for i in range(4):
            self.assertIsNone(steps[i]["predicate"])
            self.assertFalse(steps[i]["explicit_predicate"])
        self.assertTrue(steps[4]["explicit_predicate"])
        self.assertEqual(
            steps[4]["predicate"],
            {"kind": "text_present", "text": "Count: 3", "timeout_ms": 5000},
        )

    def test_parse_script_name_and_expect_and_timeout(self):
        script = """
        # comments and blank lines are ignored

        name initial launch
        launch ai.cua.fixture.notes --keep

        name tap first
        tap e7f67h expect fixture_counter 1 --timeout-ms 10000

        set_text edit_ref "hello world" --no-clear expect text_present "hello world"
        key KEYCODE_BACK
        swipe 100 200 300 400 500
        sleep 250
        """
        steps = parse_script(script)
        self.assertEqual(len(steps), 6)

        # Step 0: launch --keep
        self.assertEqual(steps[0]["name"], "initial launch")
        self.assertEqual(steps[0]["action"]["kind"], "launch")
        self.assertFalse(steps[0]["action"]["fresh"])

        # Step 1: tap with expect and timeout
        self.assertEqual(steps[1]["name"], "tap first")
        self.assertEqual(
            steps[1]["predicate"],
            {"kind": "fixture_counter", "expected": 1, "timeout_ms": 10000},
        )
        self.assertTrue(steps[1]["explicit_predicate"])

        # Step 2: set_text with --no-clear
        self.assertFalse(steps[2]["action"]["clear_first"])
        self.assertEqual(
            steps[2]["predicate"],
            {"kind": "text_present", "text": "hello world", "timeout_ms": DEFAULT_TIMEOUT_MS},
        )

        # Step 3: key
        self.assertEqual(steps[3]["action"]["keycode"], "KEYCODE_BACK")

        # Step 4: swipe
        self.assertEqual(steps[4]["action"]["from"], [100, 200])
        self.assertEqual(steps[4]["action"]["to"], [300, 400])
        self.assertEqual(steps[4]["action"]["duration_ms"], 500)

        # Step 5: sleep
        self.assertEqual(steps[5]["action"]["ms"], 250)

    def test_parse_script_bad_kind_raises_with_line_number(self):
        script = """launch ai.cua.fixture.notes
        bogus_action 123"""
        with self.assertRaises(TrailError) as ctx:
            parse_script(script)
        self.assertIn("line 2", str(ctx.exception))

    def test_derive_predicate_table(self):
        # 1. set_text -> text_present
        pred_txt = derive_predicate(
            "set_text",
            {"kind": "set_text", "ref": "r1", "text": "hello"},
            {},
        )
        self.assertEqual(pred_txt, {"kind": "text_present", "text": "hello", "timeout_ms": 5000})

        # 2. set_text with ref only -> ref_present
        pred_txt_ref = derive_predicate(
            "set_text",
            {"kind": "set_text", "ref": "r1"},
            {},
        )
        self.assertEqual(pred_txt_ref, {"kind": "ref_present", "ref": "r1", "timeout_ms": 5000})

        # 3. tap with a ref -> ref_present; tap without ref -> None
        pred_tap = derive_predicate(
            "tap",
            {"kind": "tap", "ref": "e7f67h"},
            {},
        )
        self.assertEqual(pred_tap, {"kind": "ref_present", "ref": "e7f67h", "timeout_ms": 5000})
        self.assertIsNone(derive_predicate("tap", {"kind": "tap"}, {}))

        # 4. key with a ref -> ref_present; key without ref -> None
        pred_key = derive_predicate(
            "key",
            {"kind": "key", "keycode": "KEYCODE_BACK", "ref": "k1"},
            {},
        )
        self.assertEqual(pred_key, {"kind": "ref_present", "ref": "k1", "timeout_ms": 5000})
        self.assertIsNone(derive_predicate("key", {"kind": "key", "keycode": "KEYCODE_BACK"}, {}))

        # 5. swipe with a ref -> ref_present (with custom timeout_ms); swipe without ref -> None
        pred_swipe = derive_predicate(
            "swipe",
            {"kind": "swipe", "from": [0, 0], "to": [100, 100], "ref": "s1"},
            {"timeout_ms": 3000},
        )
        self.assertEqual(pred_swipe, {"kind": "ref_present", "ref": "s1", "timeout_ms": 3000})
        self.assertIsNone(derive_predicate("swipe", {"kind": "swipe", "from": [0, 0], "to": [100, 100]}, {}))

        # 6. launch, sleep, wait_for -> None
        self.assertIsNone(derive_predicate("launch", {"kind": "launch", "package": "ai.cua.fixture.notes"}, {}))
        self.assertIsNone(derive_predicate("sleep", {"kind": "sleep", "ms": 100}, {}))
        self.assertIsNone(derive_predicate("wait_for", {"kind": "wait_for"}, {}))

    def test_non_cua_allow_apps_refused(self):
        raw_data = json.loads(FIXTURE_PATH.read_text())
        raw_data["session"]["allow_apps"] = ["com.android.settings"]
        trail = Trail.from_json(raw_data)
        self.assertEqual(trail.session["allow_apps"], ["com.android.settings"])

        raw_data["session"]["allow_apps"] = ["not_a_valid_package"]
        with self.assertRaises(TrailError) as ctx:
            Trail.from_json(raw_data)
        self.assertIn("not_a_valid_package", str(ctx.exception))

    def test_any_app_package_validation(self):
        # Valid package names
        for valid_pkg in ["com.example.app", "a.b", "ai.openclaw.app.debug", "org.test_app.pkg_1"]:
            action = validate_action({"kind": "launch", "package": valid_pkg})
            self.assertEqual(action["package"], valid_pkg)

        # Invalid package names
        for invalid_pkg in ["nodots", "123.abc", ".starts.with.dot", "ends.with.dot.", "has space.app"]:
            with self.assertRaises(TrailError):
                validate_action({"kind": "launch", "package": invalid_pkg})

    def test_launch_with_activity_and_extras(self):
        script = 'launch ai.openclaw.app.debug --activity ai.openclaw.app.MainActivity --ez openclaw.screenshotMode true --ei count 42 --es openclaw.screenshotScene chat'
        steps = parse_script(script)
        self.assertEqual(len(steps), 1)
        action = steps[0]["action"]
        self.assertEqual(action["package"], "ai.openclaw.app.debug")
        self.assertEqual(action["activity"], "ai.openclaw.app.MainActivity")
        self.assertEqual(action["extras"], {
            "openclaw.screenshotMode": True,
            "count": 42,
            "openclaw.screenshotScene": "chat",
        })

        # Extras without activity refused
        with self.assertRaises(TrailError) as ctx:
            validate_action({"kind": "launch", "package": "com.example.app", "extras": {"flag": True}})
        self.assertIn("requires activity", str(ctx.exception))

    def test_resize_action_and_script(self):
        # script forms
        s1 = parse_script("resize 1080x1920")
        self.assertEqual(s1[0]["action"], {"kind": "resize", "size": "1080x1920", "density": None})
        self.assertEqual(s1[0]["name"], "resize 1080x1920")

        s2 = parse_script("resize 1920x1080 --density 320")
        self.assertEqual(s2[0]["action"], {"kind": "resize", "size": "1920x1080", "density": 320})

        s3 = parse_script("resize reset")
        self.assertEqual(s3[0]["action"], {"kind": "resize", "size": "reset", "density": None})

        # invalid resize
        with self.assertRaises(TrailError):
            validate_action({"kind": "resize", "size": "invalid"})
        with self.assertRaises(TrailError):
            validate_action({"kind": "resize", "size": "1080x1920", "density": -1})

    def test_tap_and_set_text_label_script(self):
        script = """
        tap label:"Jump to latest"
        set_text label:"Search field" "hello"
        """
        steps = parse_script(script)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["action"], {"kind": "tap", "label": "Jump to latest"})
        self.assertEqual(steps[0]["name"], "tap Jump to latest")
        self.assertEqual(steps[1]["action"], {"kind": "set_text", "label": "Search field", "text": "hello", "clear_first": True})
        self.assertEqual(steps[1]["name"], "set_text Search field")


if __name__ == "__main__":
    unittest.main()
