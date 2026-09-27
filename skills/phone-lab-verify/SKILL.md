---
name: phone-lab-verify
description: Verify a phone-lab checkout end to end on a connected Android test device and produce a compact proof. Triggers - "verify phone-lab", "prove the viewer", "run the slice check".
---

# phone-lab verify

Run the slice-1 acceptance loop against a live phone-lab viewer and a live
Cua fixture session, then write a proof stanza. Triggers: "verify
phone-lab", "prove the viewer", "run the slice check".

## Privacy boundary

- Never print, log, or commit the device serial. Pass it only as `--serial`
  / `--device` arguments. The script below greps for it without echoing it.
- Display 0 is the human's screen. Its frames, freezes and browser
  screenshots stay under the git-ignored `runs/` directory. Publish only
  text proof (`proof/<slice>/PROOF.md`) that cites those paths.
- Drive only `ai.cua.fixture.notes` and `ai.cua.android.demo`.

## Loop

1. **Unit tests**: `python3 -m unittest discover -s tests -v` must end `OK`.
2. **Inventory**: `python3 -m phonelab inventory [--serial S]` lists the
   inner panel (logical 0, ON) and the cover panel (logical 3, usually OFF).
   No `agent` display yet. Android Studio's `studio.screen.sharing` display
   is `ignored`.
3. **Start the demo and the viewer** (both in the background, logs under
   `runs/phone-lab-runs/<run>/`):
   `python3 -m phonelab cua demo --driver <cua-driver> --duration 900 --tap-every 8 [--serial S]`
   then `python3 -m phonelab serve --port 8791 [--serial S]`.
4. **Wait for the agent display**: poll `GET /api/state` until a display
   with `role == "agent"` has `session.package == "ai.cua.fixture.notes"`.
5. **Sample** `/api/state` every 5 s for 2 minutes; record fps and
   capture_ms per display, the lease, and last-action results.
6. **Freeze**: `POST /api/freeze`; load the manifest; check schema
   `phone-lab.freeze.v1`, 2–3 panels, 64-hex sha256 on captured panels,
   `cropped_status_bar_px` 160 on the inner panel, image file > 20 KB.
7. **Inspect the composite** (open the PNG locally): every panel legible,
   labels present, status bar absent from human panels.
8. **Privacy grep**: the serial must not appear in the checkout (excluding
   `.git` and `runs/`) or in the run's manifests.
9. **Stop the demo** (`kill <pid>` sends SIGTERM; the demo stops the Cua
   session cleanly). The agent panel must leave `/api/state` within 10 s
   and the registry record under `runs/phone-lab-runs/<device-tag>/sessions/` must say
   `"state": "stopped"`.
10. **Write the proof stanza** (see below).

Steps 5, 6, 8 are automated by `scripts/verify.py`; it never starts or stops
the server or the demo and tells you what to start when the server is
unreachable.

## Pass / fail thresholds (plans/overnight-20260927-slice-1.md, phase 3)

- fps ≥ 0.8 on display 0 and on the agent display (`--min-fps`).
- At least 10 tap results `ok` during the sample (`--min-ok-taps`).
- Lease never 0 while the demo runs.
- Freeze manifest valid as in step 6.
- No serial anywhere.
- After stopping the demo: agent panel gone within 10 s, registry `stopped`.

Known limit: display 0 can miss 0.8 fps on busy content (a 4 MB PNG per
frame takes 3–4 s over adb). Record the measured number; do not lower the
threshold silently.

## Running the script

```bash
python3 skills/phone-lab-verify/scripts/verify.py --base-url http://127.0.0.1:8791 \
  --duration 120 --interval 5 --min-fps 0.8 --min-ok-taps 10 \
  --runs-dir runs/phone-lab-runs/<device-tag> [--serial S] [--skip-freeze] [--no-require-agent]
```

`--runs-dir` is the device directory the viewer banner prints (the runs
root plus the device tag), not the runs root.

Exit 0 = pass, 1 = fail, 2 = setup problem (server unreachable, serial
ambiguous). Emulators are ignored unless `--allow-emulators`; with several
phones attached pass `--serial`, `--model "Pixel 10 Pro Fold"`, or set
`ANDROID_SERIAL`. The JSON verdict is `{"result", "checks": [...], "numbers": {...}}`.

## Recording results

Append to `runs/phone-lab-runs/<run>/PROOF.md` (local) and, for a tracked
packet, `proof/<slice>/PROOF.md`:

```
## Verify <date> — <pass|fail>
- unit tests: Ran N tests, OK
- inventory: <displays and roles>
- sample: display 0 fps min/median; agent fps min/median; taps ok/total; lease min
- freeze: <manifest path>, checks passed/failed
- composite: <what was seen>
- privacy: serial grep clean (checkout, manifests)
- stop: agent panel gone after <n> s; registry <state>
- verdict JSON: <path>
```

Evidence by path only; no images in tracked proof.
