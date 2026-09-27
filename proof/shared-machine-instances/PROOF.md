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
