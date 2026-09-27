# Proof — slice 4, trace viewer

Run: 2026-09-27, cockpit Claude (worktree `confident-agnesi-094d52`, branch
`claude/confident-agnesi-094d52`, base `main` at 7202e70). Built ahead of
slice 3 because it needs no device. **No device was touched**: no `adb`, no
`cua-driver`, port 8791 never bound (the slice-2 session owned the Fold).
Raw evidence is git-ignored under `runs/phone-lab-runs/slice-4-20260927/`
(cited as `RUN/`).

## Verdict

Implemented, not proven on a device. Every acceptance item in
`plans/slice-4-trace-viewer.md` passes on synthetic runs. **Device proof
waits for slice 3**: until trails write a real `run.json` on the Fold, the
viewer has only been opened on runs from `tests/synth_run.py`.

## What was delegated versus written by the cockpit

- **Cockpit wrote**: `docs/trace-format.md` (the run-directory schema slice
  3 must write), `plans/slice-4-trace-viewer.md` (the spec), the roadmap
  pointer, the feature-map rows, this packet, and two review fixes in
  `phonelab/ui/trace.html` (clamp the step index when switching to a
  shorter run; two OFF or uncaptured panels compare as `no screen` instead
  of `screen differs`).
- **Gemini 3.8 Flash wrote** (one job via `antigravity-acp`, job
  `ac9ed8b9-3405-4261-9348-a63b7da64c34`, readiness green with
  `permission.permissionMode = approve-all`, 5 min 14 s wall clock, cleanup
  observed complete): `phonelab/trace.py`, `phonelab/ui/trace.html`,
  `tests/synth_run.py`, `tests/test_trace.py`, the `trace` subcommand in
  `phonelab/__main__.py`, and the one-line hook in `phonelab/server.py`.
  Bridge proof:
  `~/.local/state/saarius-skills/antigravity-acp-delegation/runs/ac9ed8b9-3405-4261-9348-a63b7da64c34/PROOF.md`.
- The cockpit reviewed every file in full before committing (see "Review
  findings").

## Automated tests

`python3 -m unittest discover -s tests -v` → `Ran 37 tests … OK`
(`RUN/unittest.txt`): 31 existing plus 6 new in `tests/test_trace.py`
(run index filtering and order, run and step loaders including the
`pending` marker, `safe_file` rejections, HTTP routes end-to-end on a
port-0 `TraceServer`, generator round-trip with sha256 per file, and the
`handle_get` wiring guard on `ViewerHandler.do_GET`).

## Manual check (synthetic runs, port 8792, built-in browser)

- `python3 -m tests.synth_run --runs-dir <scratch>/pl-runs --runs 2 --steps 5`
  wrote `synthetic-a-…` (5 steps, pass) and `synthetic-b-…` (drift 12 px,
  step 3 fails, step 4 skipped). `python3 -m phonelab trace --runs-dir …
  --port 8792` served them; `RUN/trace-8792.log` is the request log,
  `RUN/api-runs.json` and `RUN/api-step-3.json` are the API answers.
- `/trace` opened on the newest run: header with run picker, diff picker,
  step navigation, `Pixel 10 Pro Fold · Android 17`; timeline boxes green
  (0–2), red (3), grey (4); step 000 shows Inner Display and Cua agent
  before/after images (portrait, counter digit visible) and the Outer
  Display as `display off`.
- Diff view `#run=synthetic-b…&diff=synthetic-a…&step=3`: two aligned
  timeline rows with step 3 and 4 red-outlined where status differs; the
  summary line read `Inner Display: same screen · Cua agent: screen differs
  · Outer Display: no screen · action differs · result differs · Δ duration
  -18 ms · tree: +2 −2 refs`; left column FAIL with `counter_after 2`, right
  column OK with `counter_after 3`; the tree card highlighted the tapped
  button ref on each side with different refs and bounds (the 12 px
  drift). Page text captured by the cockpit from the browser; no screenshot
  stored (none needed, nothing personal on the page).
- Path safety with a raw HTTP client (`RUN/traversal-check.txt`):
  `steps/000/../../run.json`, `../secret.json`, `%2e%2e` segments,
  `/runs/../r1/run.json`, and a `.txt` suffix all answer 400; a step index
  of `-1` answers 400; `/api/runs/../r1` answers 404; the legitimate
  `run.json` answers 200. (A first check with `curl` showed 200 because
  curl normalises `..` client-side; the raw client is the valid check.)
- After the check: `lsof` shows no listener on 8791 at any point and none
  on 8792 after stop; `grep` for `adb`, `cua-driver`, `subprocess` over the
  new modules: 0 hits; serial-pattern grep over the new files: only the
  SurfaceFlinger ids already public in the slice-1 fixtures.

## Review findings (cockpit, on Gemini's diff)

Accepted as written: `trace.py` (does not URL-decode paths, so `%2e%2e`
fails the segment pattern; falls back to unpadded step dirs; `TraceError`
→ JSON with status; 500 never crashes a thread), `synth_run.py` (human
PNG stored cropped at 120x180 with `cropped_status_bar_px 20`; deterministic
timings; refs move with drift), `test_trace.py` (real server on port 0).
Fixed by the cockpit: step index not clamped on run change; OFF panels
reported as differing. Not changed: `j`/`k` map to previous/next as the
spec said, which is the reverse of vi habit; Bobby may want them swapped.

## Skipped or not done

- Device proof (open a slice-3 run on the Fold, diff two replays): waits
  for slice 3. Feature map rows say `implemented`, not `proven`.
- Sanitized public proof images: none; the synthetic page has nothing
  worth publishing yet.
- The live viewer (`serve`) gained the trace routes but was not started
  (needs a device); the wiring is covered by the source guard test only.
