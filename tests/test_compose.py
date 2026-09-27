"""Composite and freeze tests on synthetic displays and frames (no device)."""
from __future__ import annotations

import io
import json
import re
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from phonelab.capture import Frame
from phonelab.displays import Display
from phonelab.server import PANEL_HEIGHT, compose, freeze, recent_freezes


def _png(width: int, height: int, color: tuple, mode: str = "RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), color).save(out, format="PNG")
    return out.getvalue()


def _frame(seq: int, png: bytes, fps: float = 1.2) -> Frame:
    image = Image.open(io.BytesIO(png))
    return Frame(seq=seq, captured_at=1_790_000_000.25, capture_ms=905, png=png, jpeg=b"", width=image.width,
                 height=image.height, fps=fps)


HUMAN = Display(sf_id="1", unique_id="local:1", name="Inner Display", kind="physical", logical_id=0, width=120,
                height=200, state="ON", owner=None, status_bar_px=20, role="human")
AGENT = Display(sf_id="2", unique_id="virtual:com.android.shell,2000,Cua agent,90", name="Cua agent", kind="virtual",
                logical_id=98, width=100, height=180, state="ON", owner="com.android.shell", status_bar_px=0, role="agent")
OFF = Display(sf_id="3", unique_id="local:3", name="Outer Display", kind="physical", logical_id=3, width=60, height=120,
              state="OFF", owner=None, status_bar_px=10, role="human")
IGNORED = Display(sf_id="4", unique_id="virtual:x", name="studio.screen.sharing:0", kind="virtual", logical_id=97,
                  width=50, height=50, state="ON", owner="com.android.shell", status_bar_px=0, role="ignored")
DEVICE = {"model": "Pixel 10 Pro Fold", "android_release": "17", "api_level": 37}
SESSION = {"session_id": "abcdef12-0000", "label": "phone-lab demo", "display_id": 98, "package": "ai.cua.fixture.notes",
           "target_id": "t", "state": "active", "lease_remaining_ms": 60000, "lease_checked_at": 1.0,
           "last_action": {"kind": "tap increment", "at": 1.0, "result": "ok", "detail": {}},
           "owner": "phonelab cua demo", "updated_at": 1.0, "lease_remaining_now_ms": 41000}


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.human_png = _png(120, 200, (200, 30, 30, 255))
        self.agent_png = _png(100, 180, (30, 30, 200), mode="RGB")
        self.panels = [(HUMAN, _frame(41, self.human_png)), (AGENT, _frame(7, self.agent_png)), (OFF, None), (IGNORED, None)]

    def test_composite_and_manifest(self):
        image, manifest = compose(self.panels, DEVICE, 1_790_000_000.0, {98: SESSION})
        self.assertGreater(image.width, max(120, 100))
        self.assertGreater(image.height, PANEL_HEIGHT)
        self.assertEqual(manifest["schema"], "phone-lab.freeze.v1")
        self.assertEqual(len(manifest["panels"]), 3, "ignored displays are skipped")
        human, agent, off = manifest["panels"]
        self.assertEqual(human["cropped_status_bar_px"], 20)
        self.assertEqual(human["seq"], 41)
        self.assertEqual((human["width"], human["height"]), (120, 200))
        self.assertRegex(human["png_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsNone(human["session"])
        self.assertEqual(agent["role"], "agent")
        self.assertEqual(agent["cropped_status_bar_px"], 0)
        self.assertEqual(agent["session"]["package"], "ai.cua.fixture.notes")
        self.assertIsNone(off["seq"])
        self.assertIsNone(off["png_sha256"])
        self.assertEqual(off["name"], "Outer Display")
        self.assertEqual(manifest["device"], DEVICE)
        self.assertIsNone(manifest["image"], "compose leaves the file name to freeze()")

    def test_human_panel_is_cropped_before_scaling(self):
        image, _ = compose([(HUMAN, _frame(1, self.human_png))], DEVICE, 1_790_000_000.0)
        # 120x(200-20) scaled to height 1000 -> width 667; canvas = gutter + width + gutter
        self.assertEqual(image.width, 24 + round(120 * 1000 / 180) + 24)

    def test_long_lines_are_truncated_inside_the_panel(self):
        from phonelab.server import _fit
        from PIL import ImageFont
        font = ImageFont.load_default(size=22)
        text = "lease 55s · last tap increment ok · " * 4
        fitted = _fit(text, font, 300)
        self.assertTrue(fitted.endswith("…") and font.getlength(fitted) <= 300)
        self.assertEqual(_fit("short", font, 300), "short")

    def test_unknown_agent_session_still_renders(self):
        image, manifest = compose([(AGENT, None)], DEVICE, 1_790_000_000.0)
        self.assertIsNone(manifest["panels"][0]["session"])
        self.assertGreater(image.width, 100)


class _StubManager:
    def __init__(self, panels, sessions):
        self._panels, self._sessions = panels, sessions

    def frames(self):
        return self._panels

    def sessions_now(self, now=None):
        return self._sessions


class FreezeTests(unittest.TestCase):
    def test_freeze_writes_png_and_manifest(self):
        panels = [(HUMAN, _frame(3, _png(120, 200, (1, 2, 3, 255)))), (AGENT, _frame(4, _png(100, 180, (4, 5, 6, 255))))]
        with tempfile.TemporaryDirectory() as tmp:
            first = freeze(_StubManager(panels, {98: SESSION}), DEVICE, Path(tmp))
            second = freeze(_StubManager(panels, {98: SESSION}), DEVICE, Path(tmp))
            self.assertEqual(first["panels"], 2)
            self.assertTrue(Path(first["image"]).is_file() and Path(first["manifest"]).is_file())
            self.assertNotEqual(first["image"], second["image"], "same-second freezes get distinct names")
            manifest = json.loads(Path(first["manifest"]).read_text())
            self.assertEqual(manifest["image"], Path(first["image"]).name)
            self.assertRegex(Path(first["image"]).parent.name, r"^\d{8}$")
            self.assertRegex(Path(first["image"]).name, r"^freeze-\d{6}(-\d+)?\.png$")
            with Image.open(first["image"]) as composite:
                self.assertEqual(composite.height, 24 + 150 + 1000 + 24 + 40 + 24)
            recent = recent_freezes(Path(tmp))
            self.assertEqual(len(recent), 2)
            self.assertEqual(recent[0]["image"], Path(second["image"]).name, "newest first")


if __name__ == "__main__":
    unittest.main()
