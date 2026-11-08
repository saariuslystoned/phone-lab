"""Unit tests for TreeDumper and the treedump protocol against a fake adb script."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from phonelab.adb import Adb
from phonelab.tree import TreeDumper, TreeError, parse_reply

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "tree_cua_fixture.json"

FAKE_ADB_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]

if "push" in args:
    log = os.environ.get("FAKE_ADB_PUSH_LOG")
    if log:
        with open(log, "a") as f:
            f.write("push\n")
    sys.exit(0)

if "shell" in args:
    idx = args.index("shell")
    shell_args = args[idx + 1:]
else:
    shell_args = args

if not shell_args:
    sys.exit(0)

if shell_args[0] == "mkdir":
    sys.exit(0)

if shell_args[0] == "sha256sum":
    print("dummysha256 /data/local/tmp/phonelab/treedump.jar")
    sys.exit(0)

if shell_args[0] == "pkill":
    sys.exit(0)

if "com.pixeluse.helper.UiAutomationDaemon --stop" in " ".join(shell_args):
    busy_marker = os.environ.get("FAKE_ADB_UIA_BUSY_MARKER")
    if busy_marker and os.path.exists(busy_marker):
        try:
            os.remove(busy_marker)
        except OSError:
            pass
    stop_log = os.environ.get("FAKE_ADB_UIAD_STOP_LOG")
    if stop_log:
        with open(stop_log, "a") as f:
            f.write("stop\n")
    sys.exit(0)

busy_marker = os.environ.get("FAKE_ADB_UIA_BUSY_MARKER")
if busy_marker and os.path.exists(busy_marker):
    print(json.dumps({
        "ok": False,
        "error": "could not connect UiAutomation: UiAutomation not connected",
    }), flush=True)
    sys.exit(1)

hello = {
    "ok": True,
    "hello": "phone-lab treedump 1",
    "pid": 12345,
    "connect_flags": 1,
    "suppresses_services": False,
    "text_packages": ["ai.cua.fixture.notes", "ai.cua.android.demo"],
}
print(json.dumps(hello), flush=True)

marker = os.environ.get("FAKE_ADB_CRASH_MARKER")
if marker and os.path.exists(marker):
    try:
        os.remove(marker)
    except OSError:
        pass
    sys.exit(0)

fixture_path = os.environ.get("FAKE_ADB_FIXTURE")
fixture_tree = None
if fixture_path and os.path.exists(fixture_path):
    with open(fixture_path, "r", encoding="utf-8") as f:
        fixture_tree = json.load(f)
if not fixture_tree:
    fixture_tree = {"ok": True, "display_id": 101, "windows": [], "nodes": []}

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line == "quit":
        print(json.dumps({"ok": True, "bye": True}), flush=True)
        break
    elif line == "displays":
        print(json.dumps({"ok": True, "display_ids": [0, 101]}), flush=True)
    elif line == "tree 101":
        print("W/something: warning", flush=True)
        print(json.dumps(fixture_tree), flush=True)
    elif line.startswith("tree 7"):
        print(json.dumps({
            "ok": False,
            "error": "no windows on display 7",
            "display_id": 7,
            "windows": [],
            "nodes": [],
        }), flush=True)
    elif line.startswith("act"):
        print(json.dumps({"ok": True, "performed": True}), flush=True)
    elif line.startswith("toast"):
        print(json.dumps({"ok": True, "method": "enqueueTextToast"}), flush=True)
    else:
        print(json.dumps({"ok": False, "error": f"unknown command: {line}"}), flush=True)
"""


class ParseReplyTests(unittest.TestCase):
    def test_good_line(self):
        line = '{"ok": true, "display_ids": [0, 101]}'
        data = parse_reply(line)
        self.assertTrue(data["ok"])
        self.assertEqual(data["display_ids"], [0, 101])

    def test_ok_false_line(self):
        line = '{"ok": false, "error": "no windows on display 7", "display_id": 7}'
        data = parse_reply(line)
        self.assertFalse(data["ok"])
        self.assertEqual(data["display_id"], 7)

    def test_non_json_line(self):
        with self.assertRaises(TreeError):
            parse_reply("W/something: warning")

    def test_missing_ok(self):
        with self.assertRaises(TreeError):
            parse_reply('{"foo": "bar"}')

    def test_non_dict_json(self):
        with self.assertRaises(TreeError):
            parse_reply("[1, 2, 3]")


class TreeDumperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fake_adb = Path(cls.tmp.name) / "fake_adb"
        cls.fake_adb.write_text(FAKE_ADB_SCRIPT)
        cls.fake_adb.chmod(cls.fake_adb.stat().st_mode | stat.S_IXUSR)

        cls.jar = Path(cls.tmp.name) / "treedump.jar"
        cls.jar.write_bytes(b"dummy jar content")

        os.environ["FAKE_ADB_FIXTURE"] = str(FIXTURE_PATH)
        cls.adb = Adb(serial="FAKE", adb=str(cls.fake_adb))

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("FAKE_ADB_FIXTURE", None)
        cls.tmp.cleanup()

    def test_start_and_displays(self):
        dumper = TreeDumper(self.adb, self.jar, timeout=5.0)
        try:
            hello = dumper.start()
            self.assertTrue(hello["ok"])
            self.assertEqual(hello["hello"], "phone-lab treedump 1")
            self.assertEqual(dumper.displays(), [0, 101])
        finally:
            dumper.stop()

    def test_tree_round_trip_and_noise_skip(self):
        dumper = TreeDumper(self.adb, self.jar, timeout=5.0)
        try:
            dumper.start()
            tree = dumper.tree(101)
            self.assertTrue(tree["ok"])
            self.assertEqual(tree["display_id"], 101)
            self.assertGreater(len(tree["nodes"]), 0)
            self.assertEqual(tree["nodes"][3]["text"], "INCREMENT")

            tree7 = dumper.tree(7)
            self.assertFalse(tree7["ok"])
            self.assertIn("no windows on display 7", tree7["error"])
        finally:
            dumper.stop()

    def test_act_and_toast(self):
        dumper = TreeDumper(self.adb, self.jar, timeout=5.0)
        try:
            dumper.start()
            act_reply = dumper.act(101, 3, "click")
            self.assertTrue(act_reply["ok"])
            self.assertTrue(act_reply["performed"])

            toast_reply = dumper.toast(101, "hello")
            self.assertTrue(toast_reply["ok"])
            self.assertEqual(toast_reply["method"], "enqueueTextToast")
        finally:
            dumper.stop()

    def test_restart_once_after_eof(self):
        marker_file = Path(self.tmp.name) / "crash_once.marker"
        marker_file.write_text("crash")
        os.environ["FAKE_ADB_CRASH_MARKER"] = str(marker_file)

        dumper = TreeDumper(self.adb, self.jar, timeout=5.0)
        try:
            dumper.start()
            self.assertEqual(dumper.restarts, 0)
            tree = dumper.tree(101)
            self.assertTrue(tree["ok"])
            self.assertEqual(tree["display_id"], 101)
            self.assertEqual(dumper.restarts, 1)
        finally:
            os.environ.pop("FAKE_ADB_CRASH_MARKER", None)
            if marker_file.exists():
                marker_file.unlink()
            dumper.stop()

    def test_restart_without_start_pushes_the_jar(self):
        push_log = Path(self.tmp.name) / "push.log"
        os.environ["FAKE_ADB_PUSH_LOG"] = str(push_log)
        dumper = TreeDumper(self.adb, self.jar, timeout=5.0)
        try:
            tree = dumper.tree(101)
            self.assertTrue(tree["ok"])
            self.assertEqual(dumper.restarts, 1)
            self.assertEqual(push_log.read_text().count("push"), 1)
        finally:
            os.environ.pop("FAKE_ADB_PUSH_LOG", None)
            dumper.stop()

    def test_start_recovers_from_pixel_use_uiad_contention(self):
        busy_marker = Path(self.tmp.name) / "uia_busy.marker"
        stop_log = Path(self.tmp.name) / "uiad_stop.log"
        busy_marker.write_text("busy")
        os.environ["FAKE_ADB_UIA_BUSY_MARKER"] = str(busy_marker)
        os.environ["FAKE_ADB_UIAD_STOP_LOG"] = str(stop_log)
        dumper = TreeDumper(self.adb, self.jar, timeout=5.0)
        try:
            hello = dumper.start()
            self.assertTrue(hello["ok"])
            self.assertEqual(stop_log.read_text().count("stop"), 1)
        finally:
            os.environ.pop("FAKE_ADB_UIA_BUSY_MARKER", None)
            os.environ.pop("FAKE_ADB_UIAD_STOP_LOG", None)
            if busy_marker.exists():
                busy_marker.unlink()
            if stop_log.exists():
                stop_log.unlink()
            dumper.stop()


if __name__ == "__main__":
    unittest.main()
