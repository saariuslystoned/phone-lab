import json
import tempfile
import time
import unittest
from pathlib import Path

from phonelab.sessions import Registry, SessionRecord


def rec(sid="s1", display=98, state="active", updated=1.0, lease=60000, checked=1000.0, device_tag=None):
    return SessionRecord(session_id=sid, label="demo", display_id=display, package="ai.cua.fixture.notes",
                         target_id="t", state=state, lease_remaining_ms=lease, lease_checked_at=checked,
                         last_action={"kind": "launch", "at": 1.0, "result": "ok", "detail": {}},
                         owner="test", updated_at=updated, device_tag=device_tag)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = Registry(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_atomic(self):
        path = self.reg.write(rec())
        self.assertEqual(self.reg.load_all()[0], rec())
        self.assertEqual([p.name for p in path.parent.iterdir()], ["s1.json"])

    def test_corrupt_skipped(self):
        self.reg.write(rec())
        (self.reg.dir / "bad.json").write_text("{not json")
        self.assertEqual(len(self.reg.load_all()), 1)

    def test_by_display_latest_active(self):
        self.reg.write(rec("old", updated=1.0))
        self.reg.write(rec("new", updated=2.0))
        self.reg.write(rec("stopped", state="stopped", updated=3.0))
        self.reg.write(rec("other", display=None, updated=4.0))
        self.assertEqual({k: v.session_id for k, v in self.reg.by_display().items()}, {98: "new"})

    def test_lease_decays_and_clamps(self):
        r = rec(lease=5000, checked=1000.0)
        self.assertEqual(r.lease_remaining_now(now=1002.0), 3000)
        self.assertEqual(r.lease_remaining_now(now=1010.0), 0)
        self.assertLessEqual(rec(lease=1, checked=time.time() - 1).lease_remaining_now(), 0)

    def test_tagged_write_path_and_stamping(self):
        root = Path(self.tmp.name)
        reg = Registry(root, device_tag="pixel-10-pro-fold")
        r = rec()
        self.assertIsNone(r.device_tag)
        path = reg.write(r)
        self.assertEqual(path, root / "pixel-10-pro-fold" / "sessions" / "s1.json")
        self.assertTrue(path.exists())
        self.assertEqual(r.device_tag, "pixel-10-pro-fold")
        loaded = reg.load_all()[0]
        self.assertEqual(loaded.device_tag, "pixel-10-pro-fold")

    def test_legacy_untagged_file_is_read(self):
        root = Path(self.tmp.name)
        reg = Registry(root, device_tag="pixel-10-pro-fold")
        legacy_dir = root / "sessions"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_data = rec("legacy", display=99).to_json()
        (legacy_dir / "legacy.json").write_text(json.dumps(legacy_data))
        loaded = reg.load_all()
        self.assertEqual([x.session_id for x in loaded], ["legacy"])

    def test_foreign_tag_dropped_and_by_display_isolation(self):
        root = Path(self.tmp.name)
        reg = Registry(root, device_tag="pixel-10-pro-fold")
        reg.write(rec("mine", display=98, updated=1.0))
        # foreign session with a newer update timestamp on the same display
        foreign = rec("foreign", display=98, updated=2.0, device_tag="pixel-9")
        reg.dir.mkdir(parents=True, exist_ok=True)
        (reg.dir / "foreign.json").write_text(json.dumps(foreign.to_json()))

        all_records = reg.load_all()
        self.assertEqual([x.session_id for x in all_records], ["mine"])
        by_disp = reg.by_display()
        self.assertEqual(by_disp[98].session_id, "mine")


if __name__ == "__main__":
    unittest.main()
