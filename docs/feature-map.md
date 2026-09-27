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
| Record a session as readable steps with actions and display | `phonelab/trails.py`, `POST /api/trails` | trail schema round-trip | `runs/phone-lab-runs/<run>/trail.json` | planned |
| Deterministic replay with before/after captures and predicates | `phonelab/replay.py`, `phonelab/capture.py` | replay on recorded fixtures | five-step fixture trail replayed three times green | planned |

## Slice 4 — Trace viewer

| Feature | Modules / endpoints | Automated tests | Device proof | Status |
|---|---|---|---|---|
| Static page over a run directory: per-step screenshots, tree, action, result, timings | `phonelab/ui/trace.html`, `GET /trace/<run>` in `phonelab/server.py` | — | open a slice-3 run and diff two runs | planned |

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
  2 s. Slice 3's recorder should own the session instead.
- **Fold posture moves logical ids.** Logical 0 is the active panel (inner
  when open, cover when closed; the other is OFF under id 3 or 1). Trees
  and refs are per logical id, so a posture change is a new display.
- **Shared machine.** Emulators (`emulator-*` serials) are ignored unless
  selected with `--serial` or allowed with `--allow-emulators`; with several
  phones attached, pick one with `--serial`, `--model`, or `ANDROID_SERIAL`.
  Two phone-lab instances on one machine still need distinct `--port` and
  `--runs-dir` values (not enforced yet).
