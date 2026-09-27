# Proof — shared-machine instances

Run: 2026-09-27, cockpit Claude (worktree branch `claude/youthful-bose-e4bb20`,
commits `1e94398` spec and `a2fca5a` implementation on top of `7202e70`).
No device was touched: this slice is unit-tested only, by design. Raw run
bookkeeping is git-ignored under `runs/phone-lab-runs/shared-machine-20260927/`
(cited as `RUN/`).

## Verdict

Implemented and unit-proven. Two phone-lab instances on one machine no
longer fight over port 8791, no longer mix sessions or freezes across
devices, and a second viewer for the same device is reported instead of
silently overwriting. Not yet proven with two real phones attached (see
"Needs Bobby").

## What changed

Spec: `plans/shared-machine-instances.md` (JSON shapes, paths, messages).

| Requirement | Code | Tests |
|---|---|---|
| Busy port fails fast naming the port; `--port 0` picks a free one and prints the real URL; socket bound before any adb call | `phonelab/server.py` `PortInUse`, `bind_viewer`, `viewer_url`, `serve` (returns 2) | `tests/test_server_port.py` (3, ephemeral ports only) |
| Runs and registry keyed by a device tag from the model name, never the serial; old flat `sessions/` still read; `SessionRecord.device_tag`; `--device-tag` override | `phonelab/adb.py` `device_tag`, `Adb.tag`; `phonelab/sessions.py` `Registry(runs_root, device_tag)`; `phonelab/__main__.py`; `phonelab/cua.py` | `tests/test_adb.py` (+1 class, +4 asserts), `tests/test_sessions.py` (+3) |
| Two viewers for one device are detected and reported, not crashed | `phonelab/presence.py` `ViewerPresence` (`viewers/<pid>.json`, 5 s heartbeat from `service_actions`, 30 s stale, pid check); banner line and `/api/state.viewers` | `tests/test_presence.py` (7) |
| Docs | `README.md` run section, `docs/feature-map.md` Known limits, `AGENTS.md` (device-tag line plus the Pixel 10 Pro XL line requested through the slice-1 session), `skills/phone-lab-verify/SKILL.md` | — |

## Evidence

- `RUN/unittest-45.txt` — `Ran 45 tests … OK` (31 before the slice, 14 new),
  run by the cockpit after its own cleanups.
- `RUN/diffstat.txt` — full diff stat from `7202e70`.
- `RUN/serial-shaped-grep.txt` — grep of every tracked text file for
  10–16 character upper-alphanumeric tokens; only the two fake serials from
  `tests/test_adb.py` and two constant names remain. The adb-backed serial
  grep from `phone-lab-verify` was **not** run because this session may not
  run adb; Bobby's verify loop repeats it before merge.
- `RUN/events.jsonl`, `RUN/STATE.md`, `RUN/heartbeat` — run bookkeeping.
- Bridge proof for the delegated job:
  `~/.local/state/saarius-skills/antigravity-acp-delegation/runs/20a66ef0-8cb5-47c5-bfb1-98981f5092d9/PROOF.md`
  (completed, 88 tool calls, cleanup completed, permission mode
  `approve-all` per readiness).

## Delegated versus written by the cockpit

- **Gemini 3.8 Flash** (`antigravity-acp`, job `20a66ef0`, one delegation,
  completed on the first attempt): all code in `phonelab/` and all tests,
  following the spec.
- **Cockpit (Claude)**: the spec, all documentation edits, the review, and
  three cleanups in Gemini's output: reverted a stray parenthesis change in
  `tests/test_adb.py`, removed defensive `redact` guards Gemini added to
  the request handler (the server always has a real adb there), and added
  two docstrings. No behavioural change was needed.

## Review notes

Accepted: bind-before-adb order; registry dedupe with the device dir
winning over the legacy dir; presence pruning of dead pids and stale
heartbeats; `--device-tag` also applied to `cua demo`.
Known limits recorded in `docs/feature-map.md`: same-model phones share a
tag unless `--device-tag` is passed; legacy untagged records are shown to
every device's viewer; presence is advisory.

## Needs Bobby

1. Merge decision for `claude/youthful-bose-e4bb20` (two commits).
2. Whether to run the two-phone proof (Fold plus Pixel 10 Pro XL, `serve`
   on a non-8791 port per phone) in a device-owning lane; this session did
   not run adb. The slice-1 session relayed that the XL is open to agents;
   the AGENTS.md line reflects that, the adb relaxation was not acted on.
3. Whether `phone-lab-verify/scripts/verify.py` should read `runs_dir`
   from `/api/state` instead of needing `--runs-dir <device dir>`.

## Two-phone proof

Run: 2026-09-27, cockpit Claude (worktree branch `claude/sad-dirac-826852`),
both registered test phones on USB ADB, default runs root
(`runs/phone-lab-runs` in the worktree). Raw evidence is git-ignored under
`runs/phone-lab-runs/followups-20260927/` (cited as `RUN/`); the driver is
`RUN/two-phone.sh`, its console output `RUN/two-phone-driver.txt`.
No `cua demo` was run; the XL has no Cua runtime. Both viewers were
stopped as soon as the capture finished (`pgrep` empty afterwards).

| Check | Result | Evidence |
|---|---|---|
| Two `serve --port 0` at once bind distinct ports and print them | Fold `http://127.0.0.1:55120/`, XL `http://127.0.0.1:55121/` | `RUN/fold-viewer.log`, `RUN/xl-viewer.log` (banner lines) |
| `/api/state` reports its own device | Fold: `device_tag=pixel-10-pro-fold`, `runs_dir=runs/phone-lab-runs/pixel-10-pro-fold`, displays Inner (logical 0, ON) and Outer (logical 3, OFF). XL: `device_tag=pixel-10-pro-xl`, `runs_dir=runs/phone-lab-runs/pixel-10-pro-xl`, one display (logical 0, ON) | `RUN/fold-state.json`, `RUN/xl-state.json` |
| Freeze lands under its own device dir | Fold `pixel-10-pro-fold/20260927/freeze-090401.{png,json}` (2 panels, manifest `device.device_tag=pixel-10-pro-fold`, PNG 35 377 bytes; outer panel is a placeholder because that display is OFF with the phone open). XL `pixel-10-pro-xl/20260927/freeze-090401.{png,json}` (1 panel, `device_tag=pixel-10-pro-xl`, PNG 472 393 bytes) | `RUN/fold-freeze.json`, `RUN/xl-freeze.json`, `RUN/registry-isolation.txt` |
| Second viewer for the same device on another port | Third `serve --model "Pixel 10 Pro Fold" --port 0` bound `55170`, printed `another viewer for pixel-10-pro-fold is running: http://127.0.0.1:55120/ (pid …, heartbeat 5 s ago)`; the first viewer's `/api/state.viewers` then listed `{url: http://127.0.0.1:55170/, heartbeat_age_s: 2.3}` | `RUN/fold-viewer-2.log`, `RUN/fold-viewers-after.txt` |
| Bare `python3 -m phonelab inventory` with two phones | exit 2, `error: 2 authorized physical devices attached: Pixel 10 Pro Fold (physical), Pixel 10 Pro XL (physical); pass --serial, --model, or set ANDROID_SERIAL` (models only) | `RUN/bare-inventory.txt` |
| Registry isolation | Synthetic tagged records written to each device dir; `Registry(root, "pixel-10-pro-fold").load_all()` sees only `synth-fold-0001`, the XL registry only `synth-xl-0001` | `RUN/registry-isolation.txt` |
| No serial anywhere | Serial scan (both serials read from `adb devices -l` inside a script, never echoed): 0 hits in the checkout and 0 hits under `runs/` including both state JSONs, freeze manifests, and registry files | `RUN/serial-scan.txt` |

Note: the slice-2 session's Fold viewer on 8791 did not appear in `viewers`
because it runs from a different worktree and therefore a different runs
root. Presence is per device dir, as specified; a viewer in another
checkout is invisible, which is a known limit worth a line in
`docs/feature-map.md` if it matters.

## Two-phone proof, rerun with the XL as the primary device (2026-09-27)

Run: 2026-09-27 16:33–16:34 local, cockpit Claude (worktree branch
`claude/elegant-banach-970f2a`), both registered test phones on USB ADB,
default runs root (`runs/phone-lab-runs` in the worktree). Raw evidence is
git-ignored under `runs/phone-lab-runs/pixel-10-pro-xl/followups-20260927/`
(cited as `RUN/`); driver `RUN/two-phone.sh`, console `RUN/two-phone-driver.txt`.
The Fold viewer was read-only and lived 15 s (`RUN/fold-viewer.log`); no Cua
session and no taps were run on either phone. `pgrep -f 'phonelab serve'`
was empty at the end.

| Check | Result | Evidence |
|---|---|---|
| Two `serve --port 0` at once bind distinct ports and print them | XL `http://127.0.0.1:60877/`, Fold `http://127.0.0.1:61134/` | `RUN/xl-viewer.log`, `RUN/fold-viewer.log` |
| `/api/state` reports its own device | XL: `device_tag=pixel-10-pro-xl`, `runs_dir=runs/phone-lab-runs/pixel-10-pro-xl`, one display `Built-in Screen` (logical 0, ON, human). Fold: `device_tag=pixel-10-pro-fold`, `runs_dir=runs/phone-lab-runs/pixel-10-pro-fold`, `Inner Display` (logical 0, ON) and `Outer Display` (logical 3, OFF) | `RUN/xl-state.json`, `RUN/fold-state.json` |
| Freeze lands under its own device dir | XL `pixel-10-pro-xl/20260927/freeze-163346.{png,json}` (1 panel, manifest `device.device_tag=pixel-10-pro-xl`, status bar cropped 161 px, PNG 463 253 bytes). Fold `pixel-10-pro-fold/20260927/freeze-163401.{png,json}` (2 panels, `device_tag=pixel-10-pro-fold`, inner cropped 160 px, outer is an OFF placeholder, PNG 1 091 787 bytes) | `RUN/xl-freeze.json`, `RUN/fold-freeze.json`, `RUN/freeze-manifests.txt` |
| Second viewer for the same device on another port | Second `serve --model "Pixel 10 Pro XL" --port 0` bound `60909` and printed `another viewer for pixel-10-pro-xl is running: http://127.0.0.1:60877/ (pid …, heartbeat 5 s ago)`; the first XL viewer's `/api/state.viewers` then listed `{url: http://127.0.0.1:60909/, heartbeat_age_s: 2.7}` | `RUN/xl-viewer-2.log`, `RUN/xl-viewers-after.txt` |
| Bare `python3 -m phonelab inventory` with two phones | exit 2, `error: 2 authorized physical devices attached: Pixel 10 Pro Fold (physical), Pixel 10 Pro XL (physical); pass --serial, --model, or set ANDROID_SERIAL` (models only) | `RUN/bare-inventory.txt` |
| Registry isolation | Synthetic tagged records `synth-xl-0001` and `synth-fold-0001` written to each device dir with `display_id 0`; `Registry(root, "pixel-10-pro-xl").load_all()` returns only `synth-xl-0001`, the Fold registry only `synth-fold-0001`. `/api/state` joins sessions to agent displays only (`capture.py` `state()`), and neither phone had a Cua display live during the run, so both viewers reported no session on any display; the join path is the same `Registry.load_all` shown above | `RUN/registry-isolation.txt` |
| No serial anywhere | `RUN/serial-scan.sh` reads both serials from `adb devices -l` without echoing them and greps the whole checkout including `runs/`: 0 files for each | `RUN/serial-scan.txt` |

Two viewers on the same port were not tried (the first run already proved
`PortInUse` in `tests/test_server_port.py`); `--port 0` was used throughout
so the slice-5 session's viewer on 8791 was never touched.

### Agent-only freeze on a real device (2026-09-27 16:42)

A second read-only Fold viewer (port 63092, 12 s) still showed no Cua
display (`RUN/fold-state-3.json`), so a device-backed image with content
is still open. The endpoint itself was exercised on the Pixel 10 Pro XL
(viewer port 63168, `RUN/xl-agent-freeze-device.txt`):

- `POST /api/freeze?panels=agent` → `freeze-agent-164221.{png,json}` under
  `pixel-10-pro-xl/20260927/`, `panels: 0`, `panels_included: "agent"`,
  manifest `device.device_tag=pixel-10-pro-xl`, `panels: []`, PNG 472x1262
  (empty canvas, no human pixels).
- `POST /api/freeze?panels=bogus` → HTTP 400 `{"error": "panels must be agent or all"}`.
- `GET /api/freezes` lists the agent freeze newest first next to the
  all-panels freeze from 16:33 (`panels_included` absent on the older manifest).
- Serial scan repeated afterwards: 0 files for either serial.
