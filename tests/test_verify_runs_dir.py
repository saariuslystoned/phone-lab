"""Tests for resolve_runs_dir in skills/phone-lab-verify/scripts/verify.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills/phone-lab-verify/scripts/verify.py"
_spec = importlib.util.spec_from_file_location("verify", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_verify)
resolve_runs_dir = _verify.resolve_runs_dir


class VerifyRunsDirTests(unittest.TestCase):
    def test_flag_wins(self):
        # 1. Flag wins when passed, overriding state runs_dir
        chosen = resolve_runs_dir("/custom/override/runs", {"runs_dir": "runs/phone-lab-runs/pixel-10-pro-fold"})
        self.assertEqual(chosen, Path("/custom/override/runs"))

        chosen_no_state = resolve_runs_dir("custom/runs", {})
        self.assertEqual(chosen_no_state, Path("custom/runs"))

    def test_state_runs_dir_used(self):
        # 2. When flag is None, state runs_dir is used
        state = {"runs_dir": "runs/phone-lab-runs/pixel-10-pro-fold"}
        chosen = resolve_runs_dir(None, state)
        self.assertEqual(chosen, Path("runs/phone-lab-runs/pixel-10-pro-fold"))

    def test_fallback(self):
        # 3. When flag is None and state has no runs_dir, fallback to runs/phone-lab-runs
        chosen_empty = resolve_runs_dir(None, {})
        self.assertEqual(chosen_empty, Path("runs/phone-lab-runs"))

        chosen_none = resolve_runs_dir(None, {"runs_dir": None})
        self.assertEqual(chosen_none, Path("runs/phone-lab-runs"))


if __name__ == "__main__":
    unittest.main()
