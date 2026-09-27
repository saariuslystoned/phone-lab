"""cua-driver wrapper tests against a fake binary (no device, no network)."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from phonelab.adb import Adb
from phonelab.cua import CuaDriver, CuaError, parse_fixture_state

FAKE = r'''#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
assert argv[0] == "--device" and argv[1] == "FAKESERIAL123", argv
rest = argv[2:]
session = None
if rest and rest[0] == "--session":
    session, rest = rest[1], rest[2:]
def ok(data, **extra):
    print(json.dumps({"contract_version": "cua.android.v0", "status": "ok", "exit_code": 0, "data": data, **extra})); sys.exit(0)
def refused(reason):
    print(json.dumps({"contract_version": "cua.android.v0", "status": "refused", "exit_code": 3, "data": {}, "error": {"reason": reason}})); sys.exit(3)
if rest[:2] == ["session", "create"]:
    ok({"session_id": "sid-1", "display_id": 99, "lease_remaining_ms": 60000, "label": rest[rest.index("--label") + 1],
        "allow": [rest[i + 1] for i, a in enumerate(rest) if a == "--allow-app"]})
if rest[:2] == ["app", "launch"]:
    ok({"target_id": "tgt-1", "package": rest[rest.index("--package") + 1], "display_id": 99})
if rest[:1] == ["snapshot"]:
    ok({"snapshot_id": "snap-1", "frame_age_ms": 120, "image_base64": "AAAA" * 100, "width": 1080, "height": 1920})
if rest[:1] == ["tap"]:
    if rest[rest.index("--snapshot") + 1] == "stale":
        refused("frame_stale")
    ok({}, action={"effect": "unverifiable"})
if rest[:2] == ["session", "stop"]:
    ok({"state": "stopped", "cleanup": "released"})
print(json.dumps({"contract_version": "cua.android.v0", "status": "refused", "exit_code": 2, "data": {},
                  "error": {"message": "unsupported Android command FAKESERIAL123"}})); sys.exit(2)
'''


class CuaDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / "fake-cua-driver"
        cls.binary.write_text(FAKE)
        cls.binary.chmod(cls.binary.stat().st_mode | stat.S_IXUSR)
        cls.adb = Adb(serial="FAKESERIAL123")
        cls.driver = CuaDriver(cls.adb, cls.binary)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_argv_shapes_and_reply_fields(self):
        created = self.driver.create(["ai.cua.fixture.notes"], "phone-lab demo")["data"]
        self.assertEqual(created["label"], "phone-lab demo")
        self.assertEqual(created["allow"], ["ai.cua.fixture.notes"])
        launched = self.driver.launch("sid-1", "ai.cua.fixture.notes")["data"]
        self.assertEqual(launched["target_id"], "tgt-1")
        self.assertEqual(self.driver.stop("sid-1")["data"]["state"], "stopped")

    def test_snapshot_drops_image(self):
        data = self.driver.snapshot("sid-1", "tgt-1")["data"]
        self.assertNotIn("image_base64", data)
        self.assertEqual(data["snapshot_id"], "snap-1")

    def test_refusal_maps_to_cua_error(self):
        with self.assertRaises(CuaError) as ctx:
            self.driver.tap("sid-1", "stale", 540, 263)
        self.assertEqual((ctx.exception.status, ctx.exception.reason), ("refused", "frame_stale"))
        self.assertEqual(self.driver.tap("sid-1", "snap-1", 540, 263)["status"], "ok")

    def test_error_message_is_redacted(self):
        with self.assertRaises(CuaError) as ctx:
            self.driver.call("bogus")
        self.assertNotIn("FAKESERIAL123", str(ctx.exception))
        self.assertIn("<serial>", ctx.exception.reason)

    def test_missing_binary(self):
        with self.assertRaises(CuaError) as ctx:
            CuaDriver(self.adb, Path(self.tmp.name) / "missing").call("doctor")
        self.assertNotIn("FAKESERIAL123", str(ctx.exception))


class FixtureStateTests(unittest.TestCase):
    def test_parse_row(self):
        row = 'Row: 0 json={"counter": 3, "display_id": 99, "controls": {"increment": {"x": 540, "y": 263}}}\n'
        state = parse_fixture_state(row)
        self.assertEqual(state["counter"], 3)
        self.assertEqual(state["controls"]["increment"], {"x": 540, "y": 263})

    def test_missing_column(self):
        with self.assertRaises(ValueError):
            parse_fixture_state("No result found.\n")


if __name__ == "__main__":
    unittest.main()
