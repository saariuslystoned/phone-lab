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

- `RUN/unittest.txt`: `python3 -m unittest discover -s tests -v`, 116 tests after the slice-3 merge,
  OK. The 14 new ones are `tests/test_heal.py::HealTests` (unchanged tree
  via `refs.find`, moved 60 px, class changed, out of bound at 400 px,
  deleted node, ambiguous pair at ±60 px, inclusive bound at 120 px,
  custom bound 50 px, out-of-bound twins, `to_json` keys, `ValueError` without refs, both
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
  spec plus a compact prompt. First review found nothing to change. The slice-3 cockpit's code
  review found one low-severity ordering bug (ambiguity checked before the
  bound, so out-of-bound twins reported `ambiguous`); Gemini fixed it in a
  second job (`8decd048-3d22-4ebf-a3dd-cf8c14b3230a`) with one new test,
  and the cockpit reordered spec steps 6 and 7 to match.

## Open for Bobby

- Merge or not: the PR is additive (two new modules, one spec, one doc
  edit); nothing on `main` changes behaviour.
- The 120 px default bound and the 10 px tie window are the spec's guesses.
  Both are one-line constants; device runs after slice 3 may move them.

---

# Part 2 — integration and device proof

Run: 2026-09-27, cockpit Claude (worktree `nervous-aryabhata-b17da2`,
branch `claude/nervous-aryabhata-b17da2`, base `main` at b13c9b2, slice 3
and the slice-5 core both merged). Device: Bobby's Pixel 10 Pro Fold, open
(inner panel logical 0 ON 2076x2152, cover logical 3 OFF), pinned with
`--model "Pixel 10 Pro Fold"`; the Pixel 10 Pro XL was attached and never
selected. Raw evidence is git-ignored under
`runs/phone-lab-runs/pixel-10-pro-fold/` (cited as `DEV/`), bookkeeping
under `DEV/slice-5-20260927/` (cited as `RUN/`: `STATE.md`,
`events.jsonl`, `heartbeat`, `PROOF.md`, logs).

## Verdict

Proven. Replay heals a missing ref when the fixture's increment button
moves 40 px (status `healed`, run `pass`) and fails loudly with both trees
on disk when it moves 340 px (status `fail`, reason `out_of_bound`, run
`fail`). The trace viewer shows the `healed` badge and the heal note.

## What changed (integration)

- Trail carries the recorded node: record mode stores the resolved node
  (`i, class, text, desc, id, bounds` and the boolean flags) under
  `action.recorded` for `tap`/`set_text`; `trails.validate_action` accepts
  it. Chosen over reading the source run directory: a trail stays
  self-contained and the change is one dict copy (`plans/slice-5-self-heal.md`,
  "Integration (as built)").
- `Runner.run_step`: on a `refs.find` miss it builds a one-node recorded
  tree, calls `heal(ref, recorded_tree, tree_before, max_distance_px)`,
  writes `tree-recorded-logical-<id>.json` and
  `tree-current-logical-<id>.json`, sets `trees.heal`, and puts
  `HealResult.to_json()` under `result.detail.heal`. Healed: taps
  `refs.tap_point(res.node)`, status `healed`, `action.detail.ref_used`.
  Failed: no action, status `fail`, message names the reason, the
  distance, the bound and both tree files. Trails without `recorded` fail
  with a re-record hint.
- `trail replay --max-heal-px N` (default 120; 0 = heal only in place),
  echoed as `run.json` `heal.max_distance_px`. `record`/`replay` also
  take `--cua-size WxH` and `--cua-density N` (passed to `session create`)
  so the fixture can be laid out differently per session; this is how the
  button was moved.
- `phonelab/ui/trace.html`: `heal-note` block under Result (badge
  `heal: healed|failed`, reason, distance / bound, missing ref → used ref,
  recorded → current centre, message, candidate count).
- `docs/trace-format.md`: `detail.heal` (was `detail.healed`), `trees.heal`,
  `run.json` `heal`.

## Evidence

- `RUN/unittest.txt`: `python3 -m unittest discover -s tests -v`,
  `Ran 120 tests … OK` (116 before + `tests/test_heal_replay.py`:
  `test_healed`, `test_out_of_bound`, `test_disabled`,
  `test_no_recorded_node`, all on a `ShiftedBackend` over the captured
  fixture tree; `test_replay.test_3` now also asserts the recorded node).
- **Record** (`RUN/record.log`, `DEV/heal-demo-record-20260927-164936/`,
  `DEV/trails/heal-demo.json`): script `RUN/heal-demo.script` (launch,
  tap `e7f67h` ×2, `wait_for text_present "Count: 2"`), `--agent-only`,
  default Cua display 1080x1920 at density 320 (probed via `dumpsys
  display`, `RUN/events.jsonl`). 4/4 `ok`, 24.1 s wall. Increment node
  recorded at bounds `[24, 215, 1056, 311]`, centre (540, 263), grid
  `[540, 260]`.
- **Healed replay** (`RUN/replay-density380.log`,
  `DEV/heal-demo-replay-20260927-165012/`): `--cua-density 380`, Cua
  logical display 115. Button now at `[24, 244, 1056, 358]`, grid centre
  `[540, 300]`, new ref `d293z5`. Steps: launch `ok` 4578 ms; tap
  `healed` 4901 ms and `healed` 4967 ms (`ref e7f67h healed: moved 40 px`,
  tap at (540, 301), `fixture_counter` 1 then 2 held in 1355/1317 ms);
  wait `ok`. Run `pass`, `steps_ok 4`, 16.4 s; `heal.max_distance_px 120`.
  Each healed step has `trees.heal` = `tree-recorded-logical-115.json` +
  `tree-current-logical-115.json` (both present), note `candidates` = 1
  (`moved`, 40 px). Exit 0.
- **Loud failure** (`RUN/replay-density640.log`,
  `DEV/heal-demo-replay-20260927-165031/`): `--cua-density 640`, display
  116. Button at `[24, 499, 1056, 691]`, grid centre `[540, 600]`, ref
  `ctweup`. Step 1 `fail` in 2149 ms with `action_ms 0` (no tap):
  `ref e7f67h missing: out_of_bound (nearest candidate is 340 px away,
  bound is 120 px); trees tree-recorded-logical-116.json and
  tree-current-logical-116.json`; both files present next to
  `before-logical-116.png` / `after-logical-116.png`. Steps 2–3 `skipped`,
  run `fail`, `steps_failed 1`, 6.8 s. Exit 1.
- **Viewer** (`RUN/viewer-observation.txt`, `RUN/trace-server.log`):
  `python3 -m phonelab trace --port 0 --runs-dir DEV` bound 65511; the
  built-in browser's page text for `-165012` step 1 reads `HEALED`, the
  Result card `status: HEALED`, and the heal block `heal: healed · reason
  moved · distance 40 / 120 px · e7f67h → d293z5 · centre [540, 260] →
  [540, 300] · candidates 1`; for `-165031` step 1 `FAIL` and `heal:
  failed · out_of_bound · 340 / 120 px`. The first render showed
  `[object HTMLSpanElement]` for the badge (Gemini passed an element as
  `el()`'s text); the cockpit fixed it and re-read both pages.
- **Device left clean**: `DEV/sessions/` records 114 (record), 115, 116
  all `state: stopped`; `dumpsys display` shows 0 `Cua agent` displays
  after the runs; the density probe session was stopped too.
- **Serial scan**: `RUN/serialscan.sh` (reads both serials from `adb
  devices`, never prints them) clean on the working tree and the staged
  diff.
- **Worktree cleanup**: 259 + 5 git-ignored run files copied from
  `.claude/worktrees/wonderful-ptolemy-c83291` and `wonderful-kare-a2fadb`
  into the main checkout's `runs/phone-lab-runs/` with `rsync
  --ignore-existing` (no overwrite, every source file verified present),
  both worktrees removed, branches `claude/wonderful-ptolemy-c83291` and
  `claude/wonderful-kare-a2fadb` deleted (both were merged into `main`).
  No other worktree touched.

## Delegated versus written by the cockpit

- **Gemini 3.8 Flash** (one `antigravity-acp` job
  `292c7145-54a2-4489-b7c5-86ef15b3033c`, `approve-all`, 11 minutes, no
  device access): the `run_step` heal branch, `RunWriter.heal`,
  `Runner.max_heal_px`, `--max-heal-px`, `validate_action` `recorded`,
  the viewer heal block, `docs/trace-format.md` and the plan's
  "Integration (as built)" section, `tests/test_heal_replay.py`, the
  `test_replay.py` tweaks.
- **Cockpit**: the design (recorded node in the trail, note under
  `detail.heal`), the delegation prompt, the diff review, the
  `--cua-size`/`--cua-density` passthrough (`cua.py`, `replay.py`,
  `__main__.py`, fake driver signature), the viewer badge fix, all device
  runs, the viewer check, feature map and roadmap updates, this packet.

## Review fix (PR 9)

- Cockpit review from the slice-1 session: a non-`ok` tree reply (no
  `refs` key) made `heal()` raise `ValueError` inside `run_step`, so the
  run was `aborted` instead of the step failing. Gemini (job
  `e30a3227-3737-4e7a-8818-44c6f1107c8f`, 3 minutes) added the guard
  (`tree read failed (...); heal skipped`, no heal trees) and
  `test_tree_not_ok_fails_step` with a `FailingTreeBackend`; 121 tests OK.
  `heal()` itself still raises, as the spec says.

## Open for Bobby

- The density trick moves the button; a real fixture variant would be a
  cleaner proof but the fixture app exposes none (read-only check of
  `fixture-target` sources).
- `--max-heal-px 0` follows the spec (heal only in place), not "off": a
  `class_changed` at distance 0 would still heal.
- 120 px bound and 10 px tie window unchanged; the 40 px real-world shift
  suggests the default has headroom.
