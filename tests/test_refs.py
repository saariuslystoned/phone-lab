"""Unit tests for element reference assignment and resolution."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from phonelab.refs import (
    ALPHABET,
    REF_LEN,
    assign_refs,
    centre,
    find,
    grid,
    is_interesting,
    label_of,
    make_ref,
    ref_key,
    tap_point,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "tree_cua_fixture.json"


def load_fixture() -> dict:
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class RefsTests(unittest.TestCase):
    def test_increment_button_ref_format(self):
        tree = load_fixture()
        assign_refs(tree)
        inc_node = tree["nodes"][3]
        self.assertIn("ref", inc_node)
        ref = inc_node["ref"]
        self.assertEqual(len(ref), REF_LEN)
        self.assertTrue(all(c in ALPHABET for c in ref), f"Invalid chars in ref: {ref}")

    def test_identical_for_two_copies(self):
        tree1 = load_fixture()
        tree2 = load_fixture()
        assign_refs(tree1)
        assign_refs(tree2)
        ref1 = tree1["nodes"][3]["ref"]
        ref2 = tree2["nodes"][3]["ref"]
        self.assertEqual(ref1, ref2)

    def test_unchanged_when_counter_text_changes(self):
        tree1 = load_fixture()
        tree2 = load_fixture()
        tree2["nodes"][4]["text"] = "Count: 42"
        tree2["nodes"][4]["text_len"] = 9
        assign_refs(tree1)
        assign_refs(tree2)
        self.assertEqual(tree1["nodes"][3]["ref"], tree2["nodes"][3]["ref"])

    def test_unchanged_when_system_window_and_toast_appended(self):
        tree1 = load_fixture()
        tree2 = load_fixture()
        tree2["windows"].append({
            "w": 1,
            "id": 99,
            "type": 3,
            "type_name": "system",
            "title": "Toast",
            "package": "android",
            "layer": 1,
            "bounds": [100, 1600, 980, 1750],
            "focused": False,
            "active": False,
        })
        tree2["nodes"].append({
            "i": 5,
            "parent": None,
            "w": 1,
            "depth": 0,
            "class": "android.widget.Toast",
            "package": "android",
            "id": None,
            "text": "Saved",
            "desc": None,
            "text_len": 5,
            "bounds": [100, 1600, 980, 1750],
            "clickable": False,
            "long_clickable": False,
            "editable": False,
            "checkable": False,
            "checked": False,
            "enabled": True,
            "focusable": False,
            "focused": False,
            "visible": True,
            "scrollable": False,
            "children": 0,
        })
        assign_refs(tree1)
        assign_refs(tree2)
        self.assertEqual(tree1["nodes"][3]["ref"], tree2["nodes"][3]["ref"])

    def test_unchanged_when_bounds_shift_within_4px(self):
        tree1 = load_fixture()
        assign_refs(tree1)
        orig_ref = tree1["nodes"][3]["ref"]

        for dx, dy in [(2, -2), (-3, 1), (4, -1), (-4, -3), (1, 1), (-2, 0), (0, -2)]:
            tree = load_fixture()
            b = tree["nodes"][3]["bounds"]
            tree["nodes"][3]["bounds"] = [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]
            assign_refs(tree)
            self.assertEqual(
                tree["nodes"][3]["ref"],
                orig_ref,
                f"Ref changed at shift dx={dx}, dy={dy}",
            )

    def test_different_when_label_changes(self):
        tree1 = load_fixture()
        tree2 = load_fixture()
        tree2["nodes"][3]["text"] = "DECREMENT"
        assign_refs(tree1)
        assign_refs(tree2)
        self.assertNotEqual(tree1["nodes"][3]["ref"], tree2["nodes"][3]["ref"])

    def test_collision_handling(self):
        tree = {
            "nodes": [
                {
                    "i": 0,
                    "class": "android.widget.Button",
                    "text": "SAME",
                    "bounds": [10, 10, 100, 50],
                    "visible": True,
                    "clickable": True,
                },
                {
                    "i": 1,
                    "class": "android.widget.Button",
                    "text": "SAME",
                    "bounds": [10, 10, 100, 50],
                    "visible": True,
                    "clickable": True,
                },
                {
                    "i": 2,
                    "class": "android.widget.Button",
                    "text": "SAME",
                    "bounds": [10, 10, 100, 50],
                    "visible": True,
                    "clickable": True,
                },
            ]
        }
        res = assign_refs(tree)
        ref0 = tree["nodes"][0]["ref"]
        ref1 = tree["nodes"][1]["ref"]
        ref2 = tree["nodes"][2]["ref"]
        self.assertEqual(ref1, f"{ref0}-2")
        self.assertEqual(ref2, f"{ref0}-3")
        self.assertEqual(res["count"], 3)
        self.assertEqual(res["refs"][ref0], 0)
        self.assertEqual(res["refs"][ref1], 1)
        self.assertEqual(res["refs"][ref2], 2)

    def test_find_returns_node(self):
        tree = load_fixture()
        assign_refs(tree)
        inc_ref = tree["nodes"][3]["ref"]
        found = find(tree, inc_ref)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "ai.cua.fixture.notes:id/increment")
        self.assertIsNone(find(tree, "nonexistent"))

    def test_tap_point_is_centre(self):
        tree = load_fixture()
        inc_node = tree["nodes"][3]
        tp = tap_point(inc_node)
        self.assertEqual(tp, (540, 263))


if __name__ == "__main__":
    unittest.main()
