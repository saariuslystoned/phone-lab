# Feature map

One table per roadmap slice. Status: `proven` (device proof on the Pixel 10
Pro Fold exists at the named path), `implemented` (code and tests exist, no
device proof yet or the proof missed its target), `planned`.

## Slice 1 — Live multi-display viewer

| Feature (user-visible) | Modules / endpoints | Automated tests | Device proof | Status |
|---|---|---|---|---|
| List every display with SurfaceFlinger id, logical id, size, state, role (`python3 -m phonelab inventory`) | `phonelab/displays.py`: `parse_surfaceflinger`, `parse_dumpsys_display`, `join`, `inventory`, `to_json`; `phonelab/__main__.py` `inventory` | `tests/test_displays.py` (5 tests on captured `dumpsys` fixtures) | `runs/phone-lab-runs/overnight-20260927/inventory-before-demo.json` | proven |
| Live capture of every non-ignored display with measured fps and capture time, re-discovered every 2 s so Cua displays come and go | `phonelab/capture.py`: `DisplayCapture`, `CaptureManager.rediscover/state/frame/frames` (threads keyed on `uniqueId`) | `tests/test_capture.py` (sf id churn, OFF skip, lookup by sf id / uniqueId / `logical-<n>`) | `runs/phone-lab-runs/overnight-20260927/state-samples-2min-run2-summary.json` (Cua display 1.4–2.0 fps; inner panel 0.25 fps, see Known limits) | proven for the Cua display; implemented for display 0 (below the 0.8 fps target) |
| Viewer page: panels per display, role badges, fps/age footer, OFF placeholder, error text, connection dot, Freeze button | `phonelab/server.py`: `ViewerHandler` `GET /`, `GET /api/state`, `GET /frame/<sf_id|uniqueId|logical-n>.jpg`; `phonelab/ui/index.html` | none (vanilla page); `/api/state` shape exercised by `skills/phone-lab-verify/scripts/verify.py` | `runs/phone-lab-runs/overnight-20260927/viewer-run2-page.txt` (page text) and the cockpit's browser inspection | proven |
| Cua panel labelled with session label, package, lease countdown, last action and result; foreign Cua displays show "session unknown" | `phonelab/sessions.py`: `SessionRecord`, `Registry.write/load_all/by_display`; `phonelab/capture.py` `CaptureManager.sessions_now`; `phonelab/cua.py` `demo` writes the record | `tests/test_sessions.py` (4), `tests/test_compose.py::test_unknown_agent_session_still_renders` | `state-samples-2min-run2.jsonl` (`displays[].session` with `lease_remaining_now_ms`, `last_action`) | proven |
| Freeze-frame: labelled composite PNG plus JSON manifest with sha256 per panel, status bar cropped from human panels; last ten manifests listable | `phonelab/server.py`: `compose`, `freeze`, `recent_freezes`; `POST /api/freeze`, `GET /api/freezes` | `tests/test_compose.py` (5: manifest schema, crop, truncation, freeze files, ordering) | `runs/phone-lab-runs/overnight-20260927/freeze-validation.txt` (13/13 checks) and `freeze-proof.{png,json}` | proven |
| `python3 -m phonelab cua demo` drives the synthetic fixture on a Cua display: create, launch, renew every 10 s, tap the counter, retry stale frames, keep the registry current, clean stop | `phonelab/cua.py`: `CuaDriver.call/create/launch/inspect/renew/snapshot/tap/stop`, `fixture_state`, `demo` | `tests/test_cua.py` (7, fake `cua-driver` binary) | `runs/phone-lab-runs/overnight-20260927/demo-run1.log` (85/85 taps `ok`, counter 0→85, clean stop) | proven |
| Serial never appears in UI, logs, manifests, tests | `phonelab/adb.py` `Adb.redact`; every log line and error goes through it; `verify.py` greps the checkout | `tests/test_cua.py::test_error_message_is_redacted` | `grep -rn "$(adb get-serialno)"` clean before every commit (recorded in `proof/slice-1-live-viewer/PROOF.md`) | proven |
| Cover panel (OFF) is shown as a placeholder and never captured | `phonelab/capture.py` (`state == "OFF"` → `error "display off"`), `compose` placeholder | `tests/test_capture.py`, `tests/test_compose.py` | `state-samples-2min-run2-summary.json` (Outer Display: OFF, never captured) | proven |

## Slice 2 — Cross-display element refs

| Feature | Modules / endpoints | Automated tests | Device proof | Status |
|---|---|---|---|---|
| Accessibility tree of any display as `GET /api/tree/<logical_id>` (shell-UID UiAutomation under `app_process`, `getWindowsOnAllDisplays`, connected without suppressing the human's services; text and window titles redacted outside the two Cua apps) | `tools/treedump/` (Java, built by `build.sh`), `phonelab/tree.py` `TreeDumper`, `phonelab/server.py` `GET /api/tree/<id>`, `python3 -m phonelab tree <id>` | `tests/test_tree.py` (fake adb speaking the protocol, restart-once), `tests/test_server_tree.py` | `runs/phone-lab-runs/slice-2-20260927/tree-display0-smoke.json` (342 nodes, 0 text leaks, 43–127 ms), `tree-cap*.json` on the Cua display (10–14 ms) | proven |
| Stable short refs per element, overlay in the viewer | `phonelab/refs.py` (`ref_key`, `make_ref`, `assign_refs`, `find`), `phonelab/ui/index.html` refs toggle and chips | `tests/test_refs.py` (11), `tests/test_refs_captured.py` (ref from a captured tree equals the proven one) | `ref-stability.json` and `concurrent-taps*.json`: increment ref `e7f67h` identical across 10 captures plus a toast capture, counter text changing; `viewer-page.txt` | proven |
| Tap by ref on a Cua display while a human types on display 0 | `phonelab/server.py` `POST /api/tap` (agent displays only, registry session, cua-driver snapshot+tap, stale retry) | `tests/test_server_tree.py` (refusals, snapshot then tap at the centre, registry `tap ref`) | `concurrent-taps-run2.json`: 10/10 `ok`, counter +10, 9 of 10 taps during 10.4 s of `input -d 0 text`, typed text intact, human focus kept | proven |
| Transient windows on the agent display (`POST /api/tree/<id>/act` toast, focus, click) | `tools/treedump/` `toast`/`act`, `phonelab/server.py` | `tests/test_server_tree.py` (act focus resolves the node index) | `toast-api-cua-display-run2.png` (toast visible on the Cua display; tree and ref unchanged) | proven |

## Slice 3 — Trails: record and replay

| Feature | Modules / endpoints | Automated tests | Device proof | Status |
|---|---|---|---|---|
| Trail file (`phone-lab.trail.v1`): readable steps with action (`launch`, `tap` by ref, `set_text`, `key`, `swipe`, `wait_for`, `sleep`), display alias, predicate (`fixture_counter`, `text_present`, `ref_present`, `ref_absent`); Cua packages only; refs never coordinates | `phonelab/trails.py` (`Trail`, `Step`, `validate_action`, `validate_predicate`, `load_trail`, `save_trail`, `parse_script`, `derive_predicate`) | `tests/test_trails.py` (round-trip, validation, script grammar, derivation table, non-Cua package refused) | `runs/phone-lab-runs/pixel-10-pro-fold/trails/fixture-five.json` (recorded on the Fold; committed copy `tests/fixtures/trail_fixture_five.json`) | proven |
| `python3 -m phonelab trail record <script>`: execute a plain script live, resolve refs from fresh trees, derive predicates from the fixture oracle (counter before/after), write the trail and a `record` run | `phonelab/replay.py` (`Runner.run_step` with `derive=True`, `record`), `phonelab/__main__.py` `trail record` | `tests/test_replay.py::test_record_derives_predicates` | `runs/phone-lab-runs/pixel-10-pro-fold/fixture-five-record-20260927-092836/` (5/5 ok, predicates 0,1,2,3 + `text_present "Count: 3"`, 78.7 s) | proven |
| `python3 -m phonelab trail replay <trail> --times N`: deterministic replay without an LLM, one Cua session per run, every step re-captures every display before and after, re-resolves the ref, checks its predicate; stop-on-fail marks the rest `skipped` | `phonelab/replay.py` (`Runner`, `replay`), `trail replay` | `tests/test_replay.py` (pass run validated through `phonelab.trace` loaders and sha256; missing ref → fail + skipped; times=3; agent-only; index order) | `fixture-five-replay-20260927-{093015,093343,093453,093605}/` (4/4 pass, 20/20 steps ok, 68.6–72.2 s each with both panels; `fixture-five-replay-20260927-094218/` agent-only 20.8 s); `fixture-wrongref-replay-20260927-093732/` (fail at step 2 `ref zzzzzz not found in tree (5 refs)`, steps 3–4 skipped) | proven; the "tap step ≤ 6 s with both displays" target is missed (13–17 s, inner-panel screencap ≈ 4.2 s each) and met agent-only (4.6–4.8 s) |
| Runs written in the trace format the viewer reads (`run.json` first with `result: null`, rewritten per step; `steps/NNN/step.json`, per-display PNGs with freeze-manifest fields, `phone-lab.tree.v1`); runner owns its registry record (`owner: "phonelab trail"`, `last_action` per step) | `phonelab/replay.py` (`RunWriter`, `capture_all`, `to_tree_doc`) | `tests/test_replay.py` (loaders, sha256, crop) | the six run directories above opened by `python3 -m phonelab trace` (`slice-3-20260927/viewer-observation.txt`) | proven |

## Slice 4 — Trace viewer

| Feature | Modules / endpoints | Automated tests | Device proof | Status |
|---|---|---|---|---|
| Run-directory format that trails write and the viewer reads (`run.json`, `steps/NNN/step.json`, per-display PNGs with freeze-manifest fields, `phone-lab.tree.v1`) | `docs/trace-format.md`; `tests/synth_run.py` generates a conforming run without a device | `tests/test_trace.py` (generator round-trip: sha256 per file, files exist, steps summary) | `runs/phone-lab-runs/pixel-10-pro-fold/fixture-five-replay-20260927-093605/` written by slice 3 and read back by the viewer (`slice-3-20260927/viewer-observation.txt`) | proven |
| Trace page over a run directory: run picker, timeline, per-step before/after screenshots of every display, action, predicate, result, timings bar, lazy element tree with the acted ref highlighted | `phonelab/trace.py` (`list_runs`, `load_run`, `load_step`, `safe_file`, `handle_get`); `phonelab/ui/trace.html`; `GET /trace`, `GET /api/runs`, `GET /api/runs/<run>`, `GET /api/runs/<run>/steps/<n>`, `GET /runs/<run>/<file>` mounted in the live `ViewerHandler` and in the device-free `python3 -m phonelab trace` server (port 8792) | `tests/test_trace.py` (index, loaders, path safety, HTTP routes on port 0) | `slice-3-20260927/viewer-observation.txt`: replay run opened at step 3, both panels' before/after, action, predicate, result, timings bar, tree with `e7f67h` | proven |
| Diff two runs side by side: aligned timeline, per-display screen same/differs from `png_sha256`, action and result equality, duration delta, tree refs added/removed | `phonelab/ui/trace.html` (client-side diff from two `step.json` documents) | none (vanilla page); checked by the cockpit on synthetic runs | `slice-3-20260927/viewer-observation.txt`: replays 093605 vs 093453 at step 3 (inner display same, agent screen differs, action differs on snapshot_id, result same, Δ +1321 ms, +0 −0 refs) | proven |

## Slice 5 — Self-heal

| Feature | Modules / endpoints | Automated tests | Device proof | Status |
|---|---|---|---|---|
| Repair a missing ref from the nearest candidate within a bound, fail loudly otherwise | `phonelab/heal.py`, `phonelab/replay.py` | candidate search tests | one healed replay and one loud failure with both captures | planned |

## How to read this

A row is `proven` only when the named proof path exists under
`runs/phone-lab-runs/` (git-ignored, local) or `proof/` (tracked, text
only) and the tracked proof packet cites it. `implemented` rows have code
and passing tests but either no device run yet or a run that missed its
numeric target; the target and the measured number are in the proof
packet. Planned rows name the modules a slice will most likely touch so
that reviewers can see where new code should land.

## Known limits

- **1–2 fps ceiling, and lower on busy human screens.** Capture is one
  `adb exec-out screencap -p` per frame. The Cua display (1080x1920, flat
  UI) captures in 0.3–0.8 s. The inner panel (2076x2152) captured in 0.9 s
  on flat content when ADR 001 was written, but 3–4 s on the wallpaper home
  screen (a 4 MB PNG; raw and gzip transports measured 1.9–3.3 s over a
  12 MB/s adb link). The viewer shows the measured rate per panel instead
  of pretending otherwise.
- **No session list in Cua.** phone-lab only knows sessions that were
  created through it (the on-disk registry). Any other Cua display shows
  "session unknown to phone-lab".
- **Display-0 privacy.** Display 0 is the human's screen. Frames and
  freezes stay under git-ignored `runs/`; composites crop the status bar;
  nothing from display 0 is committed or published.
- **Cua surface swaps.** Every Cua snapshot gives the agent display a new
  SurfaceFlinger id (uniqueId and logical id stay). Capture threads follow
  uniqueId, so one transient "screencap returned no PNG" per snapshot is
  expected and the sequence continues.
- **Toast windows are invisible to the tree.** A toast is drawn on the
  display but is not an accessibility window, so `/api/tree` lists the
  same windows during a toast; the proof shows it with a screencap.
- **Demo owns its registry record.** `POST /api/tap` writes `last_action =
  tap ref`, but a running `cua demo` rewrites the record every 2 s from its
  own copy, so the viewer strip shows the demo's last action again within
  2 s. Slice 3's runner owns its own session and record instead.
- **One Cua session per replay run.** After `am force-stop` a second `app
  launch` in the same Cua session is refused with `owned_task_missing`, so
  `trail replay --times N` creates and stops one session per run (≈ 1.5 s).
- **Replay speed is capture-bound.** With both panels captured a five-step
  run takes ≈ 70 s on the Fold (inner-panel screencap ≈ 4.2 s, twice per
  step); `--agent-only` brings it to ≈ 21 s. A downscaled human capture is
  the obvious next lever.
- **Fold posture moves logical ids.** Logical 0 is the active panel (inner
  when open, cover when closed; the other is OFF under id 3 or 1). Trees
  and refs are per logical id, so a posture change is a new display.
- **Shared machine.** Emulators (`emulator-*` serials) are ignored unless
  selected with `--serial` or allowed with `--allow-emulators`; with several
  phones attached, pick one with `--serial`, `--model`, or `ANDROID_SERIAL`.
  A busy port fails fast (`--port 0` picks a free one); runs and the
  session registry live under `runs/phone-lab-runs/<device-tag>/`, where the
  tag is derived from the model name; a second viewer for the same device
  is reported (banner, `/api/state.viewers`), not refused. Still open: two
  phones of the same model share a tag unless one passes `--device-tag`;
  legacy untagged records in the flat `sessions/` directory are read by
  every device's viewer; presence is advisory, so two viewers that both
  freeze still write into the same day directory (names never collide).
  Spec: `plans/shared-machine-instances.md`.
