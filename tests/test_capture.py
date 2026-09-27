"""CaptureManager against a fake adb: threads survive SurfaceFlinger id churn and OFF displays are skipped."""
from __future__ import annotations

import io
import time
import unittest
from pathlib import Path

from PIL import Image

from phonelab.capture import CaptureManager
from phonelab.sessions import Registry

FIXTURES = Path(__file__).parent / "fixtures"
SF_TEXT = (FIXTURES / "surfaceflinger_display_id_fold_live.txt").read_text()
DD_TEXT = (FIXTURES / "dumpsys_display_fold_live_excerpt.txt").read_text()
CUA_SF_OLD = "11529215047354549223"
CUA_SF_NEW = "11529215047354549999"


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (width, height), (10, 20, 30, 255)).save(out, format="PNG")
    return out.getvalue()


class FakeAdb:
    serial, model = "FAKESERIAL123", "Pixel 10 Pro Fold"

    def __init__(self) -> None:
        self.sf_text = SF_TEXT
        self.valid = {"4619827677550801152", "4619827677550801153", CUA_SF_OLD}
        self.calls: list[str] = []

    def redact(self, text: str) -> str:
        return text.replace(self.serial, "<serial>")

    def shell(self, *args: str, timeout: float = 15) -> str:
        if args[:2] == ("dumpsys", "SurfaceFlinger"):
            return self.sf_text
        if args[:2] == ("dumpsys", "display"):
            return DD_TEXT
        raise AssertionError(args)

    def screencap(self, sf_id: str, timeout: float = 30) -> bytes | None:
        self.calls.append(sf_id)
        return _png(8, 16) if sf_id in self.valid else None


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class CaptureManagerTests(unittest.TestCase):
    def test_threads_follow_unique_id_across_sf_id_change(self):
        adb = FakeAdb()
        manager = CaptureManager(adb, Registry(Path("/nonexistent")), rediscover_every=60, max_height=1000)
        manager.rediscover()
        try:
            names = [d["name"] for d in manager.state()["displays"]]
            self.assertEqual(names, ["Inner Display", "Outer Display", "Cua agent"], "ignored display skipped")
            self.assertTrue(_wait(lambda: (manager.frame(CUA_SF_OLD) or type("F", (), {"seq": 0})).seq >= 3))
            seq_before = manager.frame(CUA_SF_OLD).seq
            # Cua replaces the surface: same uniqueId, new SurfaceFlinger id; the old id stops working.
            adb.sf_text = SF_TEXT.replace(CUA_SF_OLD, CUA_SF_NEW)
            adb.valid = {"4619827677550801152", "4619827677550801153", CUA_SF_NEW}
            manager.rediscover()
            cua = [d for d in manager.state()["displays"] if d["role"] == "agent"][0]
            self.assertEqual(cua["sf_id"], CUA_SF_NEW)
            self.assertTrue(_wait(lambda: manager.frame(CUA_SF_NEW) is not None and manager.frame(CUA_SF_NEW).seq > seq_before + 2))
            self.assertIs(manager.frame(CUA_SF_NEW), manager.frame("logical-98"))
            self.assertIs(manager.frame(CUA_SF_NEW), manager.frame(cua["unique_id"]))
            self.assertIsNone(manager.frame(CUA_SF_OLD), "the stale id no longer resolves")
            off = [d for d in manager.state()["displays"] if d["name"] == "Outer Display"][0]
            self.assertEqual((off["state"], off["error"], off["seq"]), ("OFF", "display off", None))
            self.assertNotIn("4619827677550801153", adb.calls, "OFF displays are never captured")
        finally:
            manager.stop()


if __name__ == "__main__":
    unittest.main()
