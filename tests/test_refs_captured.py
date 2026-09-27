"""The ref computed from a tree captured on the Fold matches the one the device proof recorded."""
import json
import unittest
from pathlib import Path

from phonelab.refs import assign_refs, find, tap_point

FIXTURE = Path(__file__).parent / "fixtures" / "tree_cua_fixture_captured.json"
PROVEN_INCREMENT_REF = "e7f67h"  # runs/phone-lab-runs/slice-2-20260927/ref-stability.json, 2026-09-27


class CapturedTreeRefs(unittest.TestCase):
    def test_increment_ref_matches_device_proof(self):
        tree = json.loads(FIXTURE.read_text())
        assign_refs(tree)
        node = find(tree, PROVEN_INCREMENT_REF)
        self.assertIsNotNone(node)
        self.assertTrue(node["id"].endswith("/increment"))
        self.assertEqual(tap_point(node), (540, 263))  # the fixture oracle's own increment centre


if __name__ == "__main__":
    unittest.main()
