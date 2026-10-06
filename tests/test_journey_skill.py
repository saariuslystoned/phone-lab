"""Tests for skills/phone-lab-journey/scripts (journey skeleton and results markdown)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from phonelab.sessions import Registry
from phonelab.trails import parse_script
from phonelab.replay import record, replay
from tests.test_replay import FakeBackend

_SKILL = Path(__file__).resolve().parents[1] / "skills" / "phone-lab-journey"
sys.path.insert(0, str(_SKILL / "scripts"))

import journey_results as jres  # noqa: E402
import journey_to_script as j2s  # noqa: E402

EXAMPLE_XML = _SKILL / "examples" / "fixture-increment.xml"
EXAMPLE_TRAIL = _SKILL / "examples" / "fixture-increment.trail.txt"

JOURNEY = """<journey name="fixture increment">
  <description>Press it.</description>
  <app package="ai.cua.fixture.notes"/>
  <actions>
    <action>Launch the Synthetic Notes Fixture from a fresh start.</action>
    <action>Tap the   INCREMENT
      button.</action>
    <action>Type "hello" into the note field.</action>
    <action>Verify the counter text reads "Count: 1".</action>
    <action>Check that the screen looks right.</action>
    <action>Dance around.</action>
  </actions>
</journey>"""


class JourneyToScriptTests(unittest.TestCase):
    def test_parse_normalises_whitespace(self):
        j = j2s.parse_journey(JOURNEY)
        self.assertEqual(j.name, "fixture increment")
        self.assertEqual(j.app, "ai.cua.fixture.notes")
        self.assertEqual(j.actions[1], "Tap the INCREMENT button.")
        self.assertEqual(len(j.actions), 6)

    def test_skeleton_names_every_action_and_todo_blocks_record(self):
        j = j2s.parse_journey(JOURNEY)
        text = j2s.skeleton(j, source="x.xml")
        names = [ln[len("name "):] for ln in text.splitlines() if ln.startswith("name ")]
        self.assertEqual(names, j.actions)
        lines = [ln for ln in text.splitlines() if ln and not ln.startswith(("#", "name "))]
        self.assertEqual(lines[0], "launch ai.cua.fixture.notes")
        self.assertTrue(lines[1].startswith("TODO tap"))
        self.assertTrue(lines[2].startswith("TODO set_text") and '"hello"' in lines[2])
        self.assertEqual(lines[3], 'wait_for text_present "Count: 1"')
        self.assertTrue(lines[4].startswith("TODO wait_for"))
        self.assertTrue(lines[5].startswith("TODO"))
        self.assertIn("trail 'fixture-increment'", text)
        # The skeleton is not recordable until every TODO is resolved.
        with self.assertRaises(Exception) as ctx:
            parse_script(text)
        self.assertIn("TODO", str(ctx.exception))

    def test_resolved_skeleton_parses_with_sentence_names(self):
        j = j2s.parse_journey(EXAMPLE_XML.read_text())
        text = j2s.skeleton(j).replace("TODO tap <ref from live tree>", "tap e7f67h")
        steps = parse_script(text)
        self.assertEqual([s["name"] for s in steps], j.actions)

    def test_committed_example_trail_matches_journey(self):
        j = j2s.parse_journey(EXAMPLE_XML.read_text())
        steps = parse_script(EXAMPLE_TRAIL.read_text())
        self.assertEqual([jres.base_name(s["name"]) for s in steps], j.actions)
        self.assertNotIn("TODO", EXAMPLE_TRAIL.read_text().replace("# TODO", ""))

    def test_app_allowlist_and_malformed(self):
        other = JOURNEY.replace("ai.cua.fixture.notes", "com.example.mail")
        with self.assertRaises(j2s.JourneyError):
            j2s.skeleton(j2s.parse_journey(other))
        self.assertIn("launch com.example.mail", j2s.skeleton(j2s.parse_journey(other), allow_other_app=True))
        for bad in ("<journey>", "<trail name='x'/>", "<journey name='x'/>",
                    "<journey name='x'><actions/></journey>",
                    "<journey name='x'><actions><action> </action></actions></journey>"):
            with self.assertRaises(j2s.JourneyError):
                j2s.parse_journey(bad)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.trail.txt"
            self.assertEqual(j2s.main([str(EXAMPLE_XML), "-o", str(out)]), 0)
            self.assertIn("name Tap the INCREMENT button.", out.read_text())
            bad = Path(tmp) / "bad.xml"
            bad.write_text("<nope/>")
            with redirect_stderr(io.StringIO()):
                self.assertEqual(j2s.main([str(bad)]), 2)


SENTENCES = [
    "Launch the Synthetic Notes Fixture from a fresh start.",
    "Tap the INCREMENT button.",
    "Verify the counter text reads \"Count: 1\".",
]
SCRIPT = (
    f"name {SENTENCES[0]}\nlaunch ai.cua.fixture.notes\n"
    f"name {SENTENCES[1]}\ntap e7f67h\n"
    f"name {SENTENCES[2]}\nwait_for text_present \"Count: 1\"\n"
)
JOURNEY3 = (
    "<journey name='j3'><description>d</description><actions>"
    + "".join(f"<action>{s.replace(chr(34), '&quot;')}</action>" for s in SENTENCES)
    + "</actions></journey>"
)


def _run_dirs(runs_dir: Path) -> list[Path]:
    return sorted(p for p in runs_dir.iterdir() if (p / "run.json").is_file())


class JourneyResultsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.runs = self.tmp / "runs"
        self.trails = self.tmp / "trails"
        self.registry = Registry(self.runs, "pixel-10-pro-fold")

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_and_two_replays_render_pass(self):
        backend = FakeBackend()
        self.assertEqual(record(backend, self.registry, self.runs, self.trails, "j3", SCRIPT), 0)
        self.assertEqual(replay(backend, self.registry, self.runs, self.trails / "j3.json", times=2), 0)
        dirs = _run_dirs(self.runs)
        self.assertEqual(len(dirs), 3)
        out = self.tmp / "results.md"
        journey = self.tmp / "j.xml"
        journey.write_text(JOURNEY3)
        rc = jres.main(["--journey", str(journey), *map(str, dirs), "-o", str(out)])
        self.assertEqual(rc, 0)
        md = out.read_text()
        self.assertTrue(md.startswith("# Journey: j3\n"))
        for s in SENTENCES:
            self.assertIn(f"### Action: {s} ✅", md)
        self.assertIn("`launch ai.cua.fixture.notes` (expect `fixture_counter 0`)", md)
        self.assertIn("`tap e7f67h` (expect `fixture_counter 1`)", md)
        self.assertIn("`wait_for text_present 'Count: 1'`", md)
        self.assertIn("record ok", md)
        self.assertIn("replay 2 ok", md)
        self.assertIn("**Overall: PASS**", md)

    def test_failed_replay_marks_action_and_leaves_later_unmarked(self):
        backend = FakeBackend()
        self.assertEqual(record(backend, self.registry, self.runs, self.trails, "j3", SCRIPT), 0)
        trail = json.loads((self.trails / "j3.json").read_text())
        trail["steps"][1]["action"]["ref"] = "zzzzzz"
        trail["steps"][1]["action"].pop("recorded", None)
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps(trail))
        self.assertEqual(replay(backend, self.registry, self.runs, bad, times=1), 1)
        runs = [jres.load_run_dir(p) for p in _run_dirs(self.runs)]
        md, ok = jres.render(runs, j2s.parse_journey(JOURNEY3))
        self.assertFalse(ok)
        self.assertIn(f"### Action: {SENTENCES[0]} ✅", md)
        self.assertIn(f"### Action: {SENTENCES[1]} ❌", md)
        self.assertIn("replay 1: fail", md)
        self.assertIn(f"### Action: {SENTENCES[2]}\n", md)
        self.assertIn("not evaluated in every run", md)
        self.assertIn("**Overall: FAIL**", md)

    def test_multi_step_action_and_uncompiled_action(self):
        script = (
            f"name {SENTENCES[0]}\nlaunch ai.cua.fixture.notes\n"
            f"name {SENTENCES[1]} [1/2]\ntap e7f67h\n"
            f"name {SENTENCES[1]} [2/2]\ntap e7f67h\n"
        )
        backend = FakeBackend()
        self.assertEqual(record(backend, self.registry, self.runs, self.trails, "multi", script), 0)
        runs = [jres.load_run_dir(p) for p in _run_dirs(self.runs)]
        md, ok = jres.render(runs, j2s.parse_journey(JOURNEY3))
        self.assertFalse(ok)
        self.assertIn(f"### Action: {SENTENCES[1]} ✅", md)
        self.assertIn("record ok", md)
        self.assertEqual(md.count("`tap e7f67h`"), 2)
        self.assertIn(f"### Action: {SENTENCES[2]} ❌\n- **Comment**: no compiled step", md)

    def test_missing_run_json_is_input_error(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(jres.main([str(self.tmp)]), 2)


if __name__ == "__main__":
    unittest.main()
