"""Unit tests for element self-healing."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Callable
import unittest

from phonelab.heal import heal
from phonelab import refs

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "tree_cua_fixture_captured.json"
PROVEN_INCREMENT_REF = "e7f67h"

HEALED_NOTE_KEYS = {
    "kind",
    "reason",
    "missing_ref",
    "ref",
    "max_distance_px",
    "distance_px",
    "recorded",
    "current",
    "candidates",
}

FAILED_NOTE_KEYS = {
    "kind",
    "reason",
    "missing_ref",
    "ref",
    "max_distance_px",
    "distance_px",
    "recorded",
    "current",
    "candidates",
    "message",
}


class HealTests(unittest.TestCase):
    def _make_trees(
        self, mutate_fn: Callable[[dict, dict], None] | None = None
    ) -> tuple[dict, dict]:
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            recorded = json.load(f)
        current = copy.deepcopy(recorded)
        if mutate_fn is not None:
            inc = None
            for n in current.get("nodes", []):
                if str(n.get("id", "")).endswith("/increment"):
                    inc = n
                    break
            mutate_fn(current, inc)
        refs.assign_refs(recorded)
        refs.assign_refs(current)
        return recorded, current

    def test_unchanged_tree_needs_no_heal(self) -> None:
        rec, cur = self._make_trees()
        node = refs.find(cur, PROVEN_INCREMENT_REF)
        self.assertIsNotNone(node)
        self.assertEqual(node.get("ref"), PROVEN_INCREMENT_REF)
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(res.status, "healed")
        self.assertEqual(res.note["reason"], "moved")
        self.assertEqual(res.note["distance_px"], 0)
        self.assertEqual(res.ref, PROVEN_INCREMENT_REF)
        self.assertIs(res.node, node)
        self.assertEqual(set(res.note.keys()), HEALED_NOTE_KEYS)

    def test_moved_60px_heals(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            inc["bounds"][0] += 60
            inc["bounds"][2] += 60

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(res.status, "healed")
        self.assertEqual(res.note["reason"], "moved")
        self.assertEqual(res.note["distance_px"], 60)
        self.assertEqual(res.note["ref"], res.ref)
        self.assertNotEqual(res.ref, PROVEN_INCREMENT_REF)
        self.assertEqual(res.note["current"]["centre"], [600, 260])
        self.assertEqual(res.note["recorded"]["centre"], [540, 260])
        cur_inc = [n for n in cur["nodes"] if str(n.get("id", "")).endswith("/increment")][0]
        self.assertIs(res.node, cur_inc)
        self.assertEqual(set(res.note.keys()), HEALED_NOTE_KEYS)

    def test_class_changed_heals(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            inc["class"] = "android.widget.ImageButton"

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(res.status, "healed")
        self.assertEqual(res.note["reason"], "class_changed")
        self.assertEqual(res.note["distance_px"], 0)
        self.assertEqual(res.note["current"]["class"], "android.widget.ImageButton")
        self.assertEqual(res.note["recorded"]["class"], "android.widget.Button")
        self.assertEqual(set(res.note.keys()), HEALED_NOTE_KEYS)

    def test_out_of_bound_fails(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            inc["bounds"][1] += 400
            inc["bounds"][3] += 400

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.note["reason"], "out_of_bound")
        self.assertEqual(res.note["distance_px"], 400)
        self.assertEqual(len(res.note["candidates"]), 1)
        self.assertIn("400", res.note["message"])
        self.assertIn("120", res.note["message"])
        self.assertIsNone(res.ref)
        self.assertIsNone(res.node)
        self.assertEqual(set(res.note.keys()), FAILED_NOTE_KEYS)

    def test_deleted_node_no_candidate(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            cur["nodes"] = [n for n in cur["nodes"] if not str(n.get("id", "")).endswith("/increment")]

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.note["reason"], "no_candidate")
        self.assertIsNone(res.note["distance_px"])
        self.assertEqual(res.note["candidates"], [])
        self.assertIsNone(res.ref)
        self.assertIsNone(res.node)
        self.assertEqual(res.note["message"], "no interesting node shares the recorded label")
        self.assertEqual(set(res.note.keys()), FAILED_NOTE_KEYS)

    def test_ambiguous_fails(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            cur["nodes"] = [n for n in cur["nodes"] if not str(n.get("id", "")).endswith("/increment")]
            c1 = copy.deepcopy(inc)
            c1["i"] = 998
            c1["bounds"][0] -= 60
            c1["bounds"][2] -= 60
            c2 = copy.deepcopy(inc)
            c2["i"] = 999
            c2["bounds"][0] += 60
            c2["bounds"][2] += 60
            cur["nodes"].extend([c1, c2])

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.note["reason"], "ambiguous")
        self.assertEqual(len(res.note["candidates"]), 2)
        self.assertEqual(res.note["candidates"][0]["distance_px"], 60)
        self.assertEqual(res.note["candidates"][1]["distance_px"], 60)
        self.assertEqual(res.note["distance_px"], 60)
        self.assertIsNone(res.ref)
        self.assertIsNone(res.node)
        self.assertEqual(res.note["message"], "2 candidates within 10 px of each other at 60 px")
        self.assertEqual(set(res.note.keys()), FAILED_NOTE_KEYS)

    def test_out_of_bound_twins_are_out_of_bound_not_ambiguous(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            inc["bounds"][1] += 400
            inc["bounds"][3] += 400
            twin = copy.deepcopy(inc)
            twin["i"] = 999
            twin["bounds"][1] += 5
            twin["bounds"][3] += 5
            cur["nodes"].append(twin)

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.note["reason"], "out_of_bound")
        self.assertEqual(res.note["distance_px"], 400)
        self.assertEqual(len(res.note["candidates"]), 2)
        self.assertIsNone(res.ref)
        self.assertIsNone(res.node)
        self.assertEqual(set(res.note.keys()), FAILED_NOTE_KEYS)

    def test_inclusive_bound(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            inc["bounds"][0] += 120
            inc["bounds"][2] += 120

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur, max_distance_px=120)
        self.assertEqual(res.status, "healed")
        self.assertEqual(res.note["distance_px"], 120)
        self.assertEqual(res.note["reason"], "moved")

    def test_custom_bound(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            inc["bounds"][0] += 60
            inc["bounds"][2] += 60

        rec, cur = self._make_trees(mutate)
        res = heal(PROVEN_INCREMENT_REF, rec, cur, max_distance_px=50)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.note["reason"], "out_of_bound")
        self.assertEqual(res.note["distance_px"], 60)
        self.assertIn("60", res.note["message"])
        self.assertIn("50", res.note["message"])

    def test_to_json_keys(self) -> None:
        rec, cur = self._make_trees()
        res = heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(set(res.to_json().keys()), {"status", "ref", "note"})

    def test_value_error_when_current_tree_missing_refs(self) -> None:
        rec, cur = self._make_trees()
        del cur["refs"]
        with self.assertRaises(ValueError):
            heal(PROVEN_INCREMENT_REF, rec, cur)

    def test_trees_unchanged(self) -> None:
        def mutate(cur: dict, inc: dict) -> None:
            inc["bounds"][0] += 60
            inc["bounds"][2] += 60

        rec, cur = self._make_trees(mutate)
        rec_before = copy.deepcopy(rec)
        cur_before = copy.deepcopy(cur)
        heal(PROVEN_INCREMENT_REF, rec, cur)
        self.assertEqual(rec, rec_before)
        self.assertEqual(cur, cur_before)

    def test_recorded_ref_not_in_recorded_tree(self) -> None:
        rec, cur = self._make_trees()
        res = heal("nonexistent", rec, cur)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.note["reason"], "no_candidate")
        self.assertIsNone(res.note["recorded"])
        self.assertIsNone(res.note["distance_px"])
        self.assertEqual(res.note["candidates"], [])
        self.assertEqual(res.note["message"], "recorded ref not in recorded tree")
        self.assertIsNone(res.ref)
        self.assertIsNone(res.node)
        self.assertEqual(set(res.note.keys()), FAILED_NOTE_KEYS)

    def test_empty_recorded_label_yields_no_candidate(self) -> None:
        rec, cur = self._make_trees()
        for node in rec["nodes"]:
            if str(node.get("id", "")).endswith("/increment"):
                node["text"] = None
                node["desc"] = None
                node["id"] = "noname"
        refs.assign_refs(rec)
        empty_lbl_ref = [n for n in rec["nodes"] if n.get("id") == "noname"][0]["ref"]
        res = heal(empty_lbl_ref, rec, cur)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.note["reason"], "no_candidate")
        self.assertEqual(res.note["candidates"], [])
        self.assertEqual(res.note["message"], "no interesting node shares the recorded label")


if __name__ == "__main__":
    unittest.main()
