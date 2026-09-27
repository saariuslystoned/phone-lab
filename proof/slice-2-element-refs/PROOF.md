# Proof — slice 2, cross-display element refs

Run: 2026-09-27 morning, cockpit Claude (session branch
`claude/elegant-ishizaka-9134c0`, worktree
`.claude/worktrees/elegant-ishizaka-9134c0`). Device: Bobby's registered
Pixel 10 Pro Fold on USB, Android 17 (API 37), Cua runtime already deployed;
a Pixel 10 Pro XL was attached to the same machine from mid-run and never
used. Every phonelab command was pinned with `--model "Pixel 10 Pro Fold"`.
Raw evidence is git-ignored under `runs/phone-lab-runs/slice-2-20260927/`
(cited below as `RUN/`). No image is committed; the only screencaps taken
show the Cua fixture display (no personal content) and stay local anyway.

## Verdict

Every acceptance item in `plans/slice-2-element-refs.md` is proven on the
Fold. The fixture's increment button keeps the ref `e7f67h` across ten
captures, across counter changes, and under a visible toast; ten
tap-by-ref calls landed on the Cua display while a synthetic human typed
130 characters into an editor on display 0 without losing a character or
focus. Tree reads cost 10–60 ms on the Cua display and 43–127 ms on the
human's inner panel, well under the 500 ms target; tap-by-ref costs
0.93–1.31 s end to end, under the 2 s target.

## What was delegated versus written by the cockpit

- **Spec** (`plans/slice-2-element-refs.md`): cockpit, after reading the
  evidence campaign's UiAutomation spike for the approach (no code copied).
- **Job A** (`tools/treedump/` Java program and `build.sh`,
  `phonelab/tree.py`, `phonelab/refs.py`, `tests/test_refs.py`,
  `tests/test_tree.py`, synthetic fixture): **Gemini 3.8 Flash** via
  `antigravity-acp` job `7eb97d5b-05e0-4450-ad88-f1bdb09d9e48`, ~6 min,
  permission mode `approve-all`, 49 tests green on hand-over. Bridge proof:
  `~/.local/state/saarius-skills/antigravity-acp-delegation/runs/7eb97d5b-05e0-4450-ad88-f1bdb09d9e48/PROOF.md`.
- **Job B** (`/api/tree`, `/api/tap`, `/api/tree/<id>/act`, viewer overlay,
  `tree` CLI command, `tests/test_server_tree.py`): **Gemini 3.8 Flash**,
  job `eb1665ff-3944-447b-b174-1cb814bcf671`, ~5 min, 61 tests green on
  hand-over.
- **Cockpit fixes found on the device** (all small, listed under Defects):
  main looper for `app_process`, window-title redaction, undrained stderr
  pipe, toast UI-context flag, first-tree warm-up retry, escaped quotes in
  the UI markup, SIGTERM handler for `serve`, the captured-tree test.
- **Device runs, proof scripts, and this packet**: cockpit. Scripts:
  `RUN/start_demo.py`, `RUN/ref_stability.py`, `RUN/concurrent_taps.py`.

## Automated tests

`python3 -m unittest discover -s tests -v` → `Ran 62 tests … OK`
(31 slice-1, 11 refs, 7 tree protocol with a fake adb, 12 server tree/tap
endpoints, 1 captured-tree ref pin). No device needed. `sh
tools/treedump/build.sh` builds `tools/treedump/build/treedump.jar` (7.6 KB)
with javac 21 (`--release 17`), d8 from build-tools 37, and `android.jar`
from platform 37.

## How the tree is read

`adb shell CLASSPATH=/data/local/tmp/phonelab/treedump.jar app_process
/data/local/tmp/phonelab lab.phone.treedump.Main` runs as the shell UID,
builds a `UiAutomation` on a `UiAutomationConnection` by reflection, and
connects with `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`. Hello line on the
Fold (`RUN/serve.log`, line 1): `connect_flags 1, suppresses_services
false`, so the human's accessibility services were not suppressed. The
process stays alive; the server sends `tree <logical id>` lines and reads
one JSON line back. Text, content descriptions, and window titles are
recorded only for `ai.cua.fixture.notes` and `ai.cua.android.demo`; every
other node carries `text: null` plus a `text_len`.

## Device proof

- **Inventory and posture** (`RUN/events.jsonl`): at 08:38 the phone was
  closed: Outer Display = logical 0 (ON, 1080x2364), Inner Display =
  logical 1 (OFF). Bobby then opened it and it stayed open: Inner Display =
  logical 0 (ON, 2076x2152), Outer Display = logical 3 (OFF). Logical 0 is
  the active panel, not a fixed physical one; AGENTS.md now says so.
- **Tree of the human's display** (`RUN/tree-display0-smoke.json`, via
  `TreeDumper`): 2 windows (`com.android.systemui`, the launcher), 342
  nodes, 89 refs; nodes with text or description recorded: 0 (90 nodes
  report `text_len > 0`); window titles null with `title_len`. Round trips
  447 / 127 / 51 / 43 / 69 ms (first includes warm-up). Through the API
  later: 342 nodes, 89 refs, 0 leaks.
- **Ref stability, direct** (`RUN/ref-stability.json`, `RUN/tree-cap01..10.json`,
  `RUN/tree-toast.json`; session `fcb00cf8` on logical display 103, demo
  running with `--no-taps`): 10 captures over 26 s plus one during a toast;
  increment ref `e7f67h` in every capture (bounds `[24,215,1056,311]`),
  counter text `Count: 0 → 1 → 2` after two cockpit taps through
  cua-driver in between; tree cost 11–42 ms, round trip 22–59 ms; the first
  capture after connect came back empty (defect 5, fixed).
- **Ref stability, through the API** (`RUN/concurrent-taps.json` and
  `-run2.json`, `api_captures`): 10 `GET /api/tree/103` over 22 s plus one
  capture during a toast, twice; distinct refs `["e7f67h"]` both times;
  round trips 11–53 ms; `X-Tree-Cost-Ms` 23 on the first call.
- **Toast** (`RUN/toast-cua-display.png`, `RUN/toast-api-cua-display-run2.png`,
  `RUN/tree-toast-uicontext.json`): `enqueueTextToast` via the notification
  service with `isUiContext=true` shows the toast on the Cua display (visible
  in the screencap at the bottom of the fixture screen); with `false`
  (Gemini's first version) the call succeeded but nothing appeared on that
  display. The tree during the toast lists the same single fixture window and
  the same 8 nodes: toasts are not accessibility windows, so the ref cannot
  be disturbed by one.
- **Tap by ref while a human types** (`RUN/concurrent-taps-run2.json`):
  synthetic human = `ai.cua.android.demo` started on display 0 with its
  editor focused by `input -d 0 tap`, then ten `input -d 0 text` chunks
  paced 0.9 s apart (typing span 10.4 s) in a thread while the cockpit sent
  ten `POST /api/tap {"logical_id": 103, "ref": "e7f67h"}`:

  | Measure | Result |
  |---|---|
  | Taps `ok` | 10 / 10, all at (540, 263), 0 stale retries |
  | Taps issued while typing was in progress | 9 / 10 |
  | Fixture counter | 12 → 22 (+10) |
  | Demo editor text after | exactly the 130 typed characters |
  | Demo `window_focus` / `editor_focus` | true / true, display 0 |
  | Demo event tail | only `key` and `text` events on display 0 |
  | Fixture event tail | `touch` and `increment` on display 103 only |
  | Tap wall time per call | 927–1312 ms (median 1044); `frame_age_ms` 33–121 |
  | Tree cost inside each tap | 2–11 ms |

  Run 1 (`RUN/concurrent-taps.json`) had the same tap and text results but
  the typing finished in 1.9 s so only 2 of 10 taps overlapped it; run 2 was
  added for that reason and is the cited one.
- **Viewer** (`RUN/viewer-page.txt`, built-in browser): the Cua panel with
  `refs` on shows chips `ksk67a` (editor) and `e7f67h` (increment) over the
  frame and the line `tree: 8 nodes · 5 refs · 7 ms · age 0.7 s`; clicking
  the `e7f67h` chip produced the header toast `tap e7f67h → ok (540,263)
  1234 ms` and the panel's counter went `Count: 22 → 23`. Human panels get
  the toggle too but their chips are inert.
- **Refusals** (`RUN/serve.log`): `POST /api/tap` on logical 0 →
  `{"ok": false, "reason": "not an agent display"}`; `GET /api/tree/99` →
  404.

## Stop and residue

Demo SIGTERM at 08:57:45: exited after 1.1 s (`session fcb00cf8 stopped ·
cleanup released`), agent panel gone from `/api/state` after 1.6 s,
registry record `state: stopped`, last action `stop`. Server: SIGINT was
ignored by the background process (defect 7); SIGTERM stopped it; the
device-side treedump exited with its adb pipe (`ps` on the phone shows no
`lab.phone.treedump`), `/data/local/tmp/phonelab/` removed,
`/data/local/tmp/cua-driver` holds its 9 baseline files, SurfaceFlinger
lists no virtual display, the demo app is force-stopped so display 0 is
back on the launcher, inventory shows only the two physical panels.
`serve.log`: 360 lines, 0 exceptions.

## Defects found and fixed during proof

1. `app_process` has no main looper and the accessibility client builds a
   Handler on it (`NullPointerException … Looper.mQueue`, process killed).
   Fix: `Looper.prepareMainLooper()`, command loop on a worker thread, main
   thread loops.
2. Window titles were recorded for every package; on display 0 a title can
   be personal. Fix: same allow-list as node text, `title_len` otherwise.
3. `TreeDumper` opened stderr as a pipe it never read. Fix: `DEVNULL`.
4. Toast enqueued as a non-UI context was redirected to the default display.
   Fix: `isUiContext=true`.
5. The first `tree` right after connect returned zero windows. Fix: re-ask
   once after 300 ms when a reply has no windows and no nodes.
6. Gemini's write of `index.html` escaped every attribute quote (`id=\"freeze\"`),
   so the page threw on load. Fix: unescaped 18 occurrences.
7. `serve` had no SIGTERM handler, so `kill <pid>` skipped `dumper.stop()`.
   Fix: SIGTERM raises KeyboardInterrupt, as `cua demo` already did.

## Skipped or not done

- Display 0 refs use class, id, and gridded centre only (labels are
  redacted by design); no stability run was made on display 0 because the
  human's screen changes under them.
- The Pixel 10 Pro XL was not used (different geometry check deferred).
- Public sanitized images: the two toast screencaps contain only the
  fixture screen and could be published, but were not pushed to
  `saari-co/public-oss-proof-assets` in this run.
- The demo's registry record overwrites the server's `tap ref` last action
  within 2 s (see `docs/feature-map.md`, Known limits); slice 3 should give
  the recorder ownership of the session.
