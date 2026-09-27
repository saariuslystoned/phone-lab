# Proof — slice 5, self-heal (core)

Run: 2026-09-27, cockpit Claude (worktree `wonderful-kare-a2fadb`, branch
`claude/wonderful-kare-a2fadb`, base `main` at cc98c1a). **No device was
touched**: no `adb`, no `cua-driver`; the Fold was owned by the slice-3
session and the XL was not needed. Raw evidence is git-ignored under
`runs/phone-lab-runs/slice-5-core-20260927/` (cited as `RUN/`).

## Verdict

Implemented (core), not proven on a device. `phonelab/heal.py` is the pure
candidate search the roadmap asks for; the trail-side contract that slice
3's replay will call (`--max-heal-px`, `result.detail.heal`,
`trees.heal`, hook `replay.Runner.run_step` per the slice-3 cockpit) is written in `plans/slice-5-self-heal.md`. **Integration and
device proof wait for slice 3**: the roadmap's "one healed replay and one
loud failure on the Fold" needs replay to exist first.

## Evidence

- `RUN/unittest.txt`: `python3 -m unittest discover -s tests -v`, 101 tests,
  OK. The 13 new ones are `tests/test_heal.py::HealTests` (unchanged tree
  via `refs.find`, moved 60 px, class changed, out of bound at 400 px,
  deleted node, ambiguous pair at ±60 px, inclusive bound at 120 px,
  custom bound 50 px, `to_json` keys, `ValueError` without refs, both
  trees unchanged, recorded ref absent, empty recorded label). All run on
  `tests/fixtures/tree_cua_fixture_captured.json`, the tree captured on
  the Fold in slice 2 (increment ref `e7f67h`).
- `RUN/events.jsonl`, `RUN/STATE.md`, `RUN/heartbeat`: run bookkeeping.
- Serial scan: the diff against `main` contains no token that looks like a
  device serial (the only 8+ character uppercase token is the word
  `INCREMENT`).

## What was delegated versus written by the cockpit

- **Cockpit wrote**: `plans/slice-5-self-heal.md` (spec, algorithm, exact
  note shapes, trail contract, test table), the slice-5 rows in
  `docs/feature-map.md`, this packet, and the review.
- **Gemini 3.8 Flash wrote** (one job via `antigravity-acp`, job
  `6d4f8990-363c-4e13-bb34-fd2164bcc19b`, permission mode `approve-all`,
  about 3 minutes): `phonelab/heal.py` and `tests/test_heal.py`, from the
  spec plus a compact prompt. Review found nothing to change: the ranking,
  tie rule, bound check, note keys and non-mutation all match the spec.

## Open for Bobby

- Merge or not: the PR is additive (two new modules, one spec, one doc
  edit); nothing on `main` changes behaviour.
- The 120 px default bound and the 10 px tie window are the spec's guesses.
  Both are one-line constants; device runs after slice 3 may move them.
