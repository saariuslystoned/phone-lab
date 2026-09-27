# Proof — slice 3, trails: record and replay

Run: 2026-09-27 09:19–09:43 local, cockpit Claude (session branch
`claude/wonderful-ptolemy-c83291`, worktree
`.claude/worktrees/wonderful-ptolemy-c83291`, base `origin/main` cc98c1a after
PR 3). Device: Bobby's registered Pixel 10 Pro Fold on USB, open (inner panel
logical 0 ON 2076x2152, cover logical 3 OFF), Android 17 (API 37), Cua runtime
deployed; the Pixel 10 Pro XL was attached and never used. Every phonelab
command was pinned with `--model "Pixel 10 Pro Fold"`. Raw evidence is
git-ignored under `runs/phone-lab-runs/pixel-10-pro-fold/` (cited as `DEV/`);
cockpit bookkeeping (STATE.md, events.jsonl, heartbeat, PROOF.md, logs,
scripts) under `DEV/slice-3-20260927/` (cited as `RUN/`). No image is
committed; the human-panel PNGs in the run directories show the personal
home screen and stay local.

## Verdict

Every acceptance item in `plans/slice-3-trails.md` is proven on the Fold
except the speed target with both panels captured, which is measured and
missed (see Timings). A five-step trail (launch fixture, tap `e7f67h` three
times, wait for `Count: 3`) was recorded from a plain script with predicates
derived from the fixture oracle, then replayed three times (plus one more
earlier and one agent-only run) with every step green, one trace directory
per run, all opened and diffed by the slice-4 trace viewer. A replay with a
wrong ref failed loudly at that step with the rest skipped.

## What was delegated versus written by the cockpit

- **Spec** (`plans/slice-3-trails.md`): cockpit.
- **Implementation** (`phonelab/trails.py` 457 lines, `phonelab/replay.py`
  1142 lines, `trail record|replay` in `phonelab/__main__.py`,
  `tests/test_trails.py`, `tests/test_replay.py`, the first
  `tests/fixtures/trail_fixture_five.json`): **Gemini 3.8 Flash** via
  `antigravity-acp` job `e9789416-3629-49fa-bd41-582d147c6fae`, one job,
  7 min 12 s (13:19:03–13:26:15 UTC), permission mode `approve-all`, 101
  tests green on hand-over, no reported deviation. Bridge proof:
  `~/.local/state/saarius-skills/antigravity-acp-delegation/runs/e9789416-3629-49fa-bd41-582d147c6fae/PROOF.md`.
- **Cockpit review fixes before device time** (all in `replay.py` /
  `trails.py`): record-mode tap derivation took the first oracle answer and
  faked `counter_before` as `after - 1` → now reads the oracle before the
  tap and polls until the counter changes; `capture_all` ran the inventory
  twice per phase → once; Ctrl-C during replay left `run.json` with
  `result: null` → `aborted`; a hard-coded ref in the default step name →
  removed.
- **Cockpit fix found on the device**: replay reused one Cua session for
  `--times 3` and runs 2 and 3 were refused at `launch` with
  `owned_task_missing` (`RUN/replay3-attempt1-owned-task-missing.log`;
  reproduced by hand: after `am force-stop`, a second `app launch` in the
  same session is refused). Fix: one session per run; spec, code and the
  `times=3` test updated. The two refused run directories were deleted.
- **Slice-4 fix**: `python3 -m phonelab trace --port 0` printed `:0`; now
  prints the bound port.
- **Device runs, scripts, this packet, feature map, roadmap**: cockpit.
  Serial scan script: `RUN/serial_scan.py` (reads both serials from
  `adb devices -l`, searches the checkout, never echoes them).

## Automated tests

`python3 -m unittest discover -s tests -v` → `Ran 101 tests … OK` (88
before this slice, 5 trails, 8 replay). No device needed.

## Device proof

- **Record** (`RUN/record.log`, `DEV/fixture-five-record-20260927-092836/`):
  script `RUN/fixture-five.script` (5 lines). Session on logical display
  104. 5/5 steps `ok`, run `pass`, 78.7 s. Derived predicates:
  `fixture_counter 0` (launch, oracle answered 1165 ms after launch),
  `1, 2, 3` (each tap: oracle before, then polled until it changed,
  1282–1333 ms), and the explicit `text_present "Count: 3"` held in 15 ms.
  Tap hint recorded from the resolved node: `android.widget.Button`,
  `INCREMENT`, `ai.cua.fixture.notes:id/increment`, point (540, 263).
  Trail written to `DEV/trails/fixture-five.json`; identical to the
  committed `tests/fixtures/trail_fixture_five.json`.
- **Replay ×3** (`RUN/replay3.log`; `DEV/fixture-five-replay-20260927-093343`,
  `-093453`, `-093605`; plus `-093015` from the first attempt, which also
  passed): `replay fixture-five: 3/3 pass · 68.6 s, 70.2 s, 70.1 s`,
  wall 214.8 s including session create/stop and treedump start. 15/15
  steps `ok`; `source_run_id` = the record run; `trail.sha256`
  `2464fffb…` in all three; refs resolved from a fresh tree each step
  (`tree_before_ms` 18–32 ms after the first 324–336 ms warm-up), tap point
  (540, 263) every time, `frame_stale_retries` 0 in all 12 taps; counter
  0→1→2→3 per run because `launch fresh` force-stops the fixture first.
  Cua logical display ids 105, 107, 108, 109 (one session per run).
- **Negative** (`RUN/replay-wrongref.log`,
  `DEV/fixture-wrongref-replay-20260927-093732/`): step 2 ref changed to
  `zzzzzz` → steps 0–1 `ok`, step 2 `fail` with `ref zzzzzz not found in
  tree (5 refs)`, steps 3–4 `skipped`, run `fail`, exit 1. Before and after
  captures of the failing step were still written.
- **Trace format**: each run has `run.json` (`phone-lab.run.v1`, device
  block model/release/API only), `trail.json`, `steps/000..004/step.json`,
  `before-logical-0.png` (2076x1992 after the 160 px status-bar crop),
  `after-logical-0.png`, `before-logical-<cua>.png` (1080x1920, whole),
  `tree-before-logical-<cua>.json`, `tree-after-…`; the OFF cover panel has
  a manifest entry with `seq: null` and no PNG. A run directory is ≈ 36 MB.
  Mid-run, `run.json` existed with `result: null` and a partial `steps`
  list (observed on `-093128` while it was writing).
- **Viewer** (`RUN/viewer-observation.txt`, `RUN/trace-server.log`):
  `python3 -m phonelab trace --port 0 --runs-dir runs/phone-lab-runs/pixel-10-pro-fold`
  bound port 59757; `/api/runs` listed the six runs newest first; the
  built-in browser opened replay `-093605` at step 3 with both panels'
  before/after images, action (tap `e7f67h` at 540,263), predicate,
  result, the six-phase timings bar and the tree with `Count: 3`; the
  diff against `-093453` at step 3 showed inner display same, agent screen
  differs, action differs (snapshot id), result same, Δ +1321 ms, +0 −0
  refs. Slice 4's three rows flip to proven on this.
- **Registry**: six records under `DEV/sessions/`, `owner: "phonelab
  trail"`, all `state: stopped`, `last_action` per step (the slice-2 known
  limit about the demo overwriting the record is closed for trails).

## Timings

| Measure | Both panels captured (record + 4 replays) | `--agent-only` (`-094218`) |
|---|---|---|
| Run total | 68.6–78.7 s | 20.8 s |
| launch step | 13.7–14.8 s | 4.6 s |
| tap step | 13.4–17.1 s | 4.6–4.8 s |
| wait_for step | 10.9–12.3 s | 2.0 s |
| Inner-panel screencap (2076x2152, wallpaper) | 3.5–6.7 s, median ≈ 4.2 s | skipped |
| Cua-panel screencap | 0.51–0.79 s | same |
| Action (snapshot + tap) | 1.35–1.61 s | same |
| Predicate `fixture_counter` | 1.17–1.36 s (one oracle read) | same |
| Predicate `text_present` | 12–16 ms (one tree) | same |

The spec target (tap step ≤ 6 s with both displays, run ≤ 60 s) is missed
with both panels and met agent-only; the whole gap is the inner-panel
PNG, the same limit slice 1 recorded.

## Stop and residue

Every run stopped its own session (`session stop` → `cleanup released`);
after the last run: SurfaceFlinger lists no virtual display, `ps` shows no
`lab.phone.treedump`, `/data/local/tmp/phonelab/` removed, the fixture is
force-stopped, the trace server stopped, the browser tab closed. Serial
scan (`RUN/serial_scan.py`): 2 serials checked, 0 files with a hit.

## Not done

- `set_text`, `key`, `swipe`, `sleep` are implemented and unit-tested but
  not driven on the device (the proof trail did not need them).
- The Pixel 10 Pro XL was not used.
- No public image was published (the only new screencaps include the
  personal home screen).
