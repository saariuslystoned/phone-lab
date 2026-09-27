"""Viewer presence tests: heartbeat, dead/stale file pruning, atomic writes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from phonelab.presence import HEARTBEAT_EVERY_S, SCHEMA, ViewerPresence


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.device_dir = Path(self.tmp.name)
        self.time = 1790000000.0

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_writes_json_shape(self):
        presence = ViewerPresence(self.device_dir, host="127.0.0.1", port=8792,
                                  device_tag="pixel-10-pro-fold", pid=4242,
                                  clock=lambda: self.time)
        presence.start()
        path = self.device_dir / "viewers" / "4242.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data, {
            "schema": SCHEMA,
            "pid": 4242,
            "host": "127.0.0.1",
            "port": 8792,
            "url": "http://127.0.0.1:8792/",
            "device_tag": "pixel-10-pro-fold",
            "started_at": self.time,
            "heartbeat_at": self.time,
        })

    def test_others_empty_when_alone(self):
        presence = ViewerPresence(self.device_dir, host="127.0.0.1", port=8792,
                                  device_tag="pixel-10-pro-fold", pid=4242,
                                  clock=lambda: self.time)
        presence.start()
        self.assertEqual(presence.others(), [])

    def test_foreign_live_file_reported_with_heartbeat_age(self):
        presence = ViewerPresence(self.device_dir, host="127.0.0.1", port=8792,
                                  device_tag="pixel-10-pro-fold", pid=4242,
                                  clock=lambda: self.time, pid_alive=lambda p: True)
        presence.start()

        foreign_pid = 4243
        foreign_data = {
            "schema": SCHEMA,
            "pid": foreign_pid,
            "host": "127.0.0.1",
            "port": 8793,
            "url": "http://127.0.0.1:8793/",
            "device_tag": "pixel-10-pro-fold",
            "started_at": self.time - 10.0,
            "heartbeat_at": self.time - 3.1,
        }
        (self.device_dir / "viewers" / f"{foreign_pid}.json").write_text(json.dumps(foreign_data))

        others = presence.others()
        self.assertEqual(others, [{
            "pid": 4243,
            "url": "http://127.0.0.1:8793/",
            "heartbeat_age_s": 3.1,
        }])

    def test_dead_pid_is_pruned(self):
        alive_pids = {4242}
        presence = ViewerPresence(self.device_dir, host="127.0.0.1", port=8792,
                                  device_tag="pixel-10-pro-fold", pid=4242,
                                  clock=lambda: self.time, pid_alive=lambda p: p in alive_pids)
        presence.start()

        foreign_pid = 9999
        foreign_path = self.device_dir / "viewers" / f"{foreign_pid}.json"
        foreign_data = {
            "schema": SCHEMA,
            "pid": foreign_pid,
            "host": "127.0.0.1",
            "port": 8793,
            "url": "http://127.0.0.1:8793/",
            "device_tag": "pixel-10-pro-fold",
            "started_at": self.time,
            "heartbeat_at": self.time - 2.0,
        }
        foreign_path.write_text(json.dumps(foreign_data))

        self.assertEqual(presence.others(), [])
        self.assertFalse(foreign_path.exists())

    def test_stale_heartbeat_is_pruned(self):
        presence = ViewerPresence(self.device_dir, host="127.0.0.1", port=8792,
                                  device_tag="pixel-10-pro-fold", pid=4242,
                                  clock=lambda: self.time, pid_alive=lambda p: True)
        presence.start()

        foreign_pid = 9999
        foreign_path = self.device_dir / "viewers" / f"{foreign_pid}.json"
        foreign_data = {
            "schema": SCHEMA,
            "pid": foreign_pid,
            "host": "127.0.0.1",
            "port": 8793,
            "url": "http://127.0.0.1:8793/",
            "device_tag": "pixel-10-pro-fold",
            "started_at": self.time - 100.0,
            "heartbeat_at": self.time - 35.0,
        }
        foreign_path.write_text(json.dumps(foreign_data))

        self.assertEqual(presence.others(), [])
        self.assertFalse(foreign_path.exists())

    def test_beat_only_rewrites_after_heartbeat_every_s(self):
        current_time = self.time

        def clock():
            return current_time

        presence = ViewerPresence(self.device_dir, host="127.0.0.1", port=8792,
                                  device_tag="pixel-10-pro-fold", pid=4242,
                                  clock=clock)
        presence.start()
        path = self.device_dir / "viewers" / "4242.json"
        data = json.loads(path.read_text())
        self.assertEqual(data["heartbeat_at"], self.time)

        current_time += 2.0
        presence.beat()
        data = json.loads(path.read_text())
        self.assertEqual(data["heartbeat_at"], self.time)

        current_time = self.time + HEARTBEAT_EVERY_S + 0.5
        presence.beat()
        data = json.loads(path.read_text())
        self.assertEqual(data["heartbeat_at"], current_time)

    def test_stop_removes_file_and_tolerates_missing(self):
        presence = ViewerPresence(self.device_dir, host="127.0.0.1", port=8792,
                                  device_tag="pixel-10-pro-fold", pid=4242,
                                  clock=lambda: self.time)
        presence.start()
        path = self.device_dir / "viewers" / "4242.json"
        self.assertTrue(path.exists())
        presence.stop()
        self.assertFalse(path.exists())
        presence.stop()


if __name__ == "__main__":
    unittest.main()
