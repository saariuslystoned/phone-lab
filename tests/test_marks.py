"""Unit tests for numbered tap targets (marks): filter, ordering, resolve, render, CLI glue."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from phonelab.__main__ import build_parser, run_marks, run_tap
from phonelab.displays import Display
from phonelab.marks import order, render, resolve, select, windows_for
from phonelab.tree import DEFAULT_TEXT_PACKAGES

FIXTURES = Path(__file__).resolve().parent / "fixtures"
INCREMENT_REF = "e7f67h"  # proven in slice 2 (tests/test_refs_captured.py)


def load(name: str = "tree_cua_fixture_captured.json") -> dict:
    return json.loads((FIXTURES / name).read_text())


def with_system_windows(tree: dict) -> dict:
    """Append a systemui status bar window and a nav bar button, as display 0 has."""
    tree = copy.deepcopy(tree)
    tree["windows"].append({"w": 1, "id": 7, "type": 3, "type_name": "system", "package": "com.android.systemui",
                            "layer": 1, "bounds": [0, 0, 1080, 60], "focused": False, "active": False})
    base = len(tree["nodes"])
    tree["nodes"].append({"i": base, "parent": None, "w": 1, "depth": 0, "class": "android.widget.FrameLayout",
                          "package": "com.android.systemui", "id": "com.android.systemui:id/status_bar",
                          "text": None, "desc": None, "text_len": 0, "bounds": [0, 0, 1080, 60],
                          "clickable": False, "visible": True})
    tree["nodes"].append({"i": base + 1, "parent": base, "w": 1, "depth": 1, "class": "android.widget.ImageView",
                          "package": "com.android.systemui", "id": "com.android.systemui:id/battery",
                          "text": None, "desc": None, "text_len": 4, "bounds": [980, 10, 1060, 50],
                          "clickable": True, "visible": True})
    return tree


def png_bytes(width: int = 1080, height: int = 1920) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (250, 250, 250)).save(out, format="PNG")
    return out.getvalue()


class SelectTests(unittest.TestCase):
    def test_fixture_marks_and_increment_number(self):
        marks = select(load())
        self.assertEqual([m["n"] for m in marks], [1, 2, 3, 4])
        self.assertEqual([m["label"] for m in marks],
                         ["Synthetic Notes Fixture", "Synthetic notes only", "INCREMENT", "Count: 0"])
        inc = marks[2]
        self.assertEqual(inc["ref"], INCREMENT_REF)
        self.assertEqual(inc["tap"], [540, 263])
        self.assertEqual(inc["bounds"], [24, 215, 1056, 311])
        self.assertEqual(set(inc), {"n", "ref", "label", "class", "bounds", "tap", "i", "package", "actionable"})

    def test_full_window_containers_skipped(self):
        refs = {m["ref"] for m in select(load())}
        tree = load()
        select(tree)
        for node in tree["nodes"]:
            if node["bounds"] == [0, 0, 1080, 1920]:
                self.assertNotIn(node.get("ref"), refs)

    def test_system_windows_excluded_by_default_included_by_flag(self):
        tree = with_system_windows(load())
        self.assertEqual(windows_for(tree), {0})
        default = select(copy.deepcopy(tree))
        self.assertTrue(all(m["package"] == "ai.cua.fixture.notes" for m in default))
        self.assertEqual(len(default), 4)
        wide = select(copy.deepcopy(tree), include_system=True)
        self.assertIn("com.android.systemui", {m["package"] for m in wide})
        self.assertEqual(wide[0]["label"], "battery")  # top row comes first
        self.assertEqual(wide[3]["ref"], INCREMENT_REF)  # the status-bar node shifts numbers only when asked

    def test_unfocused_app_window_skipped_when_another_is_focused(self):
        tree = load()
        tree["windows"][0]["focused"] = False
        tree["windows"].append({"w": 1, "type_name": "application", "package": "ai.cua.android.demo",
                                "bounds": [0, 0, 1080, 1920], "focused": True})
        self.assertEqual(windows_for(tree), {1})
        self.assertEqual(select(tree), [])

    def test_no_windows_list_marks_all(self):
        tree = load()
        del tree["windows"]
        self.assertEqual(len(select(tree)), 4)

    def test_label_child_folds_into_clickable_parent(self):
        tree = load()
        inc = tree["nodes"][6]
        inc["text"], inc["id"] = None, None
        tree["nodes"].append({"i": 8, "parent": 6, "w": 0, "depth": 5, "class": "android.widget.TextView",
                              "package": "ai.cua.fixture.notes", "id": None, "text": "Tap me", "desc": None,
                              "text_len": 6, "bounds": [40, 230, 300, 290], "clickable": False, "visible": True})
        marks = select(tree)
        self.assertEqual(len(marks), 4)
        self.assertEqual(marks[2]["label"], "Tap me")
        self.assertEqual(marks[2]["class"], "android.widget.Button")


class OrderTests(unittest.TestCase):
    def test_stable_across_node_order_and_small_jitter(self):
        a = select(load())
        tree = load()
        tree["nodes"] = list(reversed(tree["nodes"]))
        b = select(tree)
        self.assertEqual([(m["n"], m["ref"]) for m in a], [(m["n"], m["ref"]) for m in b])

    def test_row_tolerance_orders_left_to_right(self):
        marks = [{"ref": "c", "bounds": [500, 110, 600, 160]},
                 {"ref": "a", "bounds": [10, 100, 100, 150]},
                 {"ref": "b", "bounds": [300, 118, 400, 170]},
                 {"ref": "d", "bounds": [10, 200, 100, 250]}]
        self.assertEqual([m["ref"] for m in order(marks)], ["a", "b", "c", "d"])
        self.assertEqual([m["ref"] for m in order(marks, tolerance=5)], ["a", "c", "b", "d"])

    def test_two_captures_same_numbering_when_counter_changes(self):
        a = select(load())
        tree = load()
        tree["nodes"][7]["text"] = "Count: 3"
        b = select(tree)
        self.assertEqual([(m["n"], m["i"]) for m in a], [(m["n"], m["i"]) for m in b])
        self.assertEqual(resolve(a, "#3")["ref"], resolve(b, "#3")["ref"])  # the counter's own ref follows its text


class ResolveTests(unittest.TestCase):
    def test_resolve_number_ref_and_misses(self):
        marks = select(load())
        self.assertEqual(resolve(marks, "#3")["ref"], INCREMENT_REF)
        self.assertEqual(resolve(marks, INCREMENT_REF)["n"], 3)
        self.assertIsNone(resolve(marks, "#99"))
        self.assertIsNone(resolve(marks, "zzzzzz"))
        with self.assertRaises(ValueError):
            resolve(marks, "#x")


class RenderTests(unittest.TestCase):
    def test_dims_crop_and_drawing(self):
        marks = select(load())
        plain = Image.open(io.BytesIO(render(png_bytes(), marks)))
        self.assertEqual(plain.size, (1080, 1920))
        self.assertNotEqual(plain.getpixel((24, 263)), (250, 250, 250))  # box edge drawn
        self.assertEqual(plain.getpixel((540, 1000)), (250, 250, 250))  # empty area untouched
        cropped = Image.open(io.BytesIO(render(png_bytes(), marks, crop_status_bar_px=60)))
        self.assertEqual(cropped.size, (1080, 1860))

    def test_badge_kept_below_crop(self):
        marks = [{"n": 1, "ref": "a", "bounds": [100, 10, 400, 200]}]
        img = Image.open(io.BytesIO(render(png_bytes(), marks, crop_status_bar_px=80)))
        self.assertNotEqual(img.getpixel((104, 4)), (250, 250, 250))  # badge at the new top edge


class FakeDumper:
    def __init__(self, tree: dict):
        self._tree = tree

    def tree(self, logical_id: int) -> dict:
        return copy.deepcopy(self._tree)


class FakeAdb:
    def __init__(self):
        self.calls = []

    def redact(self, text: str) -> str:
        return text

    def screencap(self, sf_id: str):
        self.calls.append(("screencap", sf_id))
        return png_bytes()

    def shell(self, *args, timeout: float = 15):
        self.calls.append(("shell",) + args)
        return ""


DISPLAY0 = Display(sf_id="0", unique_id="local:0", name="Built-in",
                   kind="physical", logical_id=0, width=1080, height=1920, state="ON", owner=None,
                   status_bar_px=60, role="human")


class CliTests(unittest.TestCase):
    def test_parse_marks_and_tap(self):
        p = build_parser()
        a = p.parse_args(["marks", "0", "--model", "Pixel 10 Pro Fold", "--out", "/tmp/x.png", "--include-system"])
        self.assertEqual((a.command, a.logical_id, a.out, a.include_system, a.model), ("marks", 0, "/tmp/x.png", True,
                                                                                     "Pixel 10 Pro Fold"))
        t = p.parse_args(["tap", "0", "#3", "--expect-ref", INCREMENT_REF, "--allow-emulators"])
        self.assertEqual((t.command, t.target, t.expect_ref, t.allow_emulators, t.include_system),
                         ("tap", "#3", INCREMENT_REF, True, False))

    def test_run_marks_writes_overlay_and_json(self):
        args = build_parser().parse_args(["marks", "0"])
        adb = FakeAdb()
        with tempfile.TemporaryDirectory() as tmp:
            code, res = run_marks(adb, FakeDumper(load()), args, Path(tmp), display_lookup=lambda _a: [DISPLAY0])
            self.assertEqual(code, 0)
            self.assertEqual(res["count"], 4)
            self.assertEqual(Image.open(res["png"]).size, (1080, 1860))
            self.assertEqual(json.loads(Path(res["json"]).read_text())["marks"][2]["ref"], INCREMENT_REF)
            self.assertTrue(res["png"].startswith(str(Path(tmp) / "marks")))
        self.assertEqual(adb.calls, [("screencap", DISPLAY0.sf_id)])

    def test_run_marks_refuses_missing_display(self):
        args = build_parser().parse_args(["marks", "5"])
        code, res = run_marks(FakeAdb(), FakeDumper(load()), args, Path("/nonexistent"), display_lookup=lambda _a: [DISPLAY0])
        self.assertEqual(code, 1)
        self.assertFalse(res["ok"])

    def test_run_tap_taps_with_input_d(self):
        args = build_parser().parse_args(["tap", "0", "#3", "--expect-ref", INCREMENT_REF])
        adb = FakeAdb()
        code, res = run_tap(adb, FakeDumper(load()), args, DEFAULT_TEXT_PACKAGES)
        self.assertEqual(code, 0)
        self.assertEqual(res["ref"], INCREMENT_REF)
        self.assertEqual(adb.calls, [("shell", "input", "-d", "0", "tap", "540", "263")])

    def test_run_tap_refuses_stale_expect_ref(self):
        args = build_parser().parse_args(["tap", "0", "#2", "--expect-ref", INCREMENT_REF])
        adb = FakeAdb()
        code, res = run_tap(adb, FakeDumper(load()), args, DEFAULT_TEXT_PACKAGES)
        self.assertEqual((code, res["refused"]), (3, "stale"))
        self.assertEqual(adb.calls, [])

    def test_run_tap_refuses_other_package_and_unknown(self):
        adb = FakeAdb()
        tree = with_system_windows(load())
        args = build_parser().parse_args(["tap", "0", "#1", "--include-system"])
        code, res = run_tap(adb, FakeDumper(tree), args, DEFAULT_TEXT_PACKAGES)
        self.assertEqual((code, res["refused"]), (3, "package"))
        code, res = run_tap(adb, FakeDumper(load()), build_parser().parse_args(["tap", "0", "#9"]), DEFAULT_TEXT_PACKAGES)
        self.assertEqual(code, 1)
        self.assertEqual(adb.calls, [])


if __name__ == "__main__":
    unittest.main()
