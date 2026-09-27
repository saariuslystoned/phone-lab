# Overnight run 2026-09-27: finish slice 1 to a working prototype

Bobby's go-ahead (2026-09-26 evening) accepts [ADR 001](../docs/adr-001-tech-stack.md)
and the [roadmap](roadmap.md). Goal by morning: the live multi-display viewer
proven on the Fold with a live Cua session, a feature map, a verification
skill, and a proof packet. The cockpit (Claude session) decides, reviews,
runs the device, and writes proof. Gemini 3.8 Flash writes code and docs
through the `antigravity-acp` MCP lane (skill
`saarius-skills:antigravity-acp-delegation`, default model
`gemini-3.8-flash-high`, break-glass `approve-all` already configured).

## Ground rules

- Work only inside the assigned worktree and `runs/phone-lab-runs/`.
- Never print, log, or commit the device serial. Model name only.
- Drive only `ai.cua.fixture.notes` and `ai.cua.android.demo`.
- Keyguard: swipe-only may be swiped away; PIN means `WAITING_FOR_HUMAN`.
- Commit on the session branch only; never merge to `main`; do not push.
- One Gemini job at a time in this workspace. Wait for the canonical
  result; review the diff and run tests yourself before committing.
- If the same Gemini job fails twice (or the lane is red), write that job
  yourself and record the fallback in `PROOF.md`.
- Long run bookkeeping under `runs/phone-lab-runs/overnight-20260927/`:
  `STATE.md` (phase, blockers), `events.jsonl` (one line per phase/job),
  `heartbeat` (touch every phase), `PROOF.md` (claims with evidence paths).

## Phases

1. **Setup.** `git merge --ff-only claude/great-dijkstra-3b655a` (or a plain
   merge if it moved). `python3 -m unittest discover -s tests -v` (9 tests).
   `adb devices -l` shows one `device`; Cua doctor via
   `~/Developer/worktrees/cua-bobby-jellyware/libs/cua-driver/rust/target/debug/cua-driver --device $(adb get-serialno) doctor`
   returns `status: ok`. `antigravity_acp_readiness` for this worktree is
   green. Create the run directory files.
2. **Job B (Gemini).** Prompt below. Review: tests pass, every module
   imports, no serial anywhere (`grep -rn "$(adb get-serialno)" . --exclude-dir=.git`
   must print nothing), diff matches the spec. Commit.
3. **Device proof (cockpit).**
   - `python3 -m phonelab inventory` lists Inner Display (logical 0),
     Outer Display (logical 3, OFF), and no agent display yet.
   - Start `python3 -m phonelab cua demo --driver <cua-driver> --duration 900 --tap-every 8`
     in the background (log to the run dir), then
     `python3 -m phonelab serve --port 8791` in the background.
   - Poll `curl -s localhost:8791/api/state` until a display with role
     `agent` appears with `session.package == "ai.cua.fixture.notes"`.
   - Sample `/api/state` every 5 s for 2 minutes: record fps and capture_ms
     for display 0 and the agent display, lease values, last_action results.
     Pass: fps ≥ 0.8 on both, at least 10 tap results `ok`, lease never 0.
   - `curl -s -X POST localhost:8791/api/freeze`; validate the manifest
     (schema, 2 or 3 panels, sha256s, cropped_status_bar_px 160 for the
     inner panel) and open the composite with the Read tool to inspect it:
     both panels legible, labels present, status bar absent.
   - Open `http://127.0.0.1:8791/` in the built-in browser if available and
     screenshot it into the run dir (local only; never commit).
   - Stop the demo; confirm the agent panel disappears from `/api/state`
     within 10 s and the registry record says `stopped`.
   - Fix small defects yourself; delegate larger ones as a bounded Gemini
     job with the failing evidence. Re-run the proof after any fix.
4. **Soak (cockpit).** Viewer plus demo for 30 minutes; log fps min/median,
   capture errors, frame_stale retries, registry writes. Pass: no viewer
   crash, no capture thread death, fps median ≥ 0.8.
5. **Job C (Gemini).** Feature map, verification skill, README update.
   Prompt below. Review, then install the skill additively:
   `ln -s "$PWD/skills/phone-lab-verify" ~/.claude/skills/phone-lab-verify`
   only if that path does not already exist. Run the skill's `verify.py`
   against the live server as the final check.
6. **Wrap.** Fill `proof/slice-1-live-viewer/PROOF.md` (text only; evidence
   by path; numbers from phases 3–4; fallbacks and skipped items named).
   Mark ADR 001 `Accepted (2026-09-26)`; tick roadmap boxes that are proven.
   Final commit. Final `STATE.md`: `phase: done` or the exact blocker. Leave
   the device with no live Cua session and no residue under
   `/data/local/tmp/cua-driver` beyond the runtime itself.

## Job B prompt (Gemini)

```
You are implementing job B of slice 1 in the public MIT repo phone-lab.
Repo: github.com/saariuslystoned/phone-lab. Worktree (your cwd): <WORKTREE>. Branch: <BRANCH>.

Read first, in this order: AGENTS.md, plans/slice-1-live-viewer.md (the spec; follow its module contracts exactly), then the finished modules phonelab/adb.py, phonelab/displays.py, phonelab/sessions.py (use their APIs as they are; do not modify them).

Deliver exactly these new files:
1. phonelab/capture.py — spec section "phonelab/capture.py" (Frame, DisplayCapture thread, CaptureManager with rediscovery every 2 s via displays.inventory, start/stop, state(), frame(), frames()). Android screencap PNGs are RGBA: convert to RGB before JPEG. fps from the timestamps of the last 10 captures. When display.state == "OFF" do not capture; set error "display off".
2. phonelab/server.py — spec section "phonelab/server.py": ThreadingHTTPServer; routes GET /, GET /api/state, GET /frame/<sf_id>.jpg, POST /api/freeze, GET /api/freezes; composite and manifest exactly as specified (PIL.ImageFont.load_default(size=...); Pillow 12 is installed). Put the composite builder in a pure function compose(panels, device, now) -> (PIL.Image, manifest_dict) where panels is a list of (Display, Frame | None); freeze(manager, device, runs_dir) calls it and writes the files. Expose serve(adb, registry, host, port, runs_dir, max_height). One request-log line per request to stdout, never a serial.
3. phonelab/ui/index.html — spec section "phonelab/ui/index.html". Vanilla HTML/CSS/JS, dark theme, no external resources, panels wrap on narrow widths. Poll /api/state every 500 ms; swap a panel's <img> src to /frame/<sf_id>.jpg?seq=<seq> only when seq changes; lease countdown ticks client-side; Freeze button POSTs /api/freeze and shows the returned image path in a toast.
4. phonelab/cua.py — spec section "phonelab/cua.py": CuaDriver (subprocess to the cua-driver binary, JSON parsing, drop data.image_base64, CuaError with redacted message on non-zero exit_code), fixture_state(), demo(...) exactly as specified (create with label "phone-lab demo" and --allow-app ai.cua.fixture.notes, launch, registry write after every loop iteration with the fresh lease from inspect, renew every 10 s, increment taps every tap_every_s with frame_stale retry up to 3, last_action recording, clean stop on duration end or KeyboardInterrupt, state "stopped" written at the end).
5. phonelab/__main__.py — spec section "phonelab/__main__.py": subcommands inventory | serve | cua demo with the listed flags, PHONELAB_CUA_DRIVER env fallback for --driver, default runs dir runs/phone-lab-runs relative to the cwd. inventory prints displays.to_json for each display as a JSON list.
6. tests/test_compose.py — two synthetic Display+Frame pairs from small PIL images (a physical human panel with status_bar_px 20; an agent panel with a session dict) plus one OFF display with Frame None. Assert: composite wider than either input; manifest schema "phone-lab.freeze.v1" with 3 panels; human panel cropped_status_bar_px 20; png_sha256 is 64 hex chars; the OFF panel has seq None. Imports: phonelab, standard library, PIL only.

Constraints: Python 3.12, standard library plus Pillow only; type hints, dataclasses, short docstrings. Do NOT run adb, cua-driver, or anything touching a device; do not start the server; no network. Tests must pass with no device attached. The serial must never appear in logs, JSON, HTML, or exceptions: route error text through adb.redact. Do not modify existing files. Do not commit. No new dependencies or virtualenv.

Finish by running: python3 -m unittest discover -s tests -v ; and python3 -c "import phonelab.capture, phonelab.server, phonelab.cua, phonelab.__main__"
Report compactly: files created, the unittest summary line, and any spec ambiguity you resolved and how.
```

## Job C prompt (Gemini)

```
You are finishing slice 1 of the public MIT repo phone-lab with documentation and a verification skill.
Repo: github.com/saariuslystoned/phone-lab. Worktree (your cwd): <WORKTREE>. Branch: <BRANCH>.

Read first: README.md, AGENTS.md, plans/roadmap.md, plans/slice-1-live-viewer.md, docs/adr-001-tech-stack.md, and the phonelab/ package (every module) and tests/.

Deliver exactly these files:
1. docs/feature-map.md — one table per roadmap slice. Columns: Feature (user-visible behaviour), Modules / endpoints, Automated tests, Device proof (path pattern under runs/phone-lab-runs or proof/), Status (proven | implemented | planned). Slice 1 rows must name real functions, routes, and tests from the code you read; slices 2–5 rows are "planned" with the modules they will most likely touch. End with a short "How to read this" paragraph and a "Known limits" list (1–2 fps ceiling, no session list in Cua, display-0 privacy).
2. skills/phone-lab-verify/SKILL.md — a Claude Code skill with YAML frontmatter (name: phone-lab-verify; description: verify a phone-lab checkout end to end on a connected Android test device and produce a compact proof; triggers: "verify phone-lab", "prove the viewer", "run the slice check"). Body: the verification loop (unit tests; inventory; start cua demo and serve; wait for the agent display; sample /api/state; freeze; validate manifest; inspect composite; privacy grep for the serial; stop demo; confirm the panel disappears; write a proof stanza), the pass/fail thresholds from plans/overnight-20260927-slice-1.md phase 3, the privacy boundary (never publish serials or human-screen captures), and how to record results.
3. skills/phone-lab-verify/scripts/verify.py — standard-library Python 3.12 (urllib, json, subprocess, argparse). Flags: --base-url (default http://127.0.0.1:8791), --duration (seconds to sample, default 120), --interval (default 5), --min-fps (default 0.8), --min-ok-taps (default 10), --runs-dir (default runs/phone-lab-runs), --require-agent (default true), --skip-freeze. It samples /api/state, tracks per-display fps/capture_ms/errors, agent session lease and last_action results, then POSTs /api/freeze (unless skipped), loads the manifest and checks schema, panel count, sha256 lengths, cropped_status_bar_px on human panels, and that the image file exists and is larger than 20 KB. It greps the repo (excluding .git and runs) and the run manifests for the serial obtained from `adb get-serialno` without ever printing it. It prints a compact JSON verdict {"result": "pass"|"fail", "checks": [...], "numbers": {...}} and exits 0 on pass, 1 on fail, 2 on a setup problem (server unreachable). It never starts or stops the server or the demo itself; it says what to start when the server is unreachable.
4. README.md — replace "Status: just started." with a short Status section (slice 1 prototype: what works, how to run: `python3 -m phonelab serve`, `python3 -m phonelab cua demo --driver ...`, tests) and a link list to docs/adr-001-tech-stack.md, docs/feature-map.md, plans/roadmap.md, and skills/phone-lab-verify/SKILL.md. Keep everything else in README.md as it is.

Constraints: Do not touch phonelab/ or tests/. Do NOT run adb or the server. Standard library only in verify.py. No serials anywhere. Do not commit.
Finish by running: python3 -m py_compile skills/phone-lab-verify/scripts/verify.py && python3 skills/phone-lab-verify/scripts/verify.py --help
Report compactly: files created and any ambiguity you resolved.
```
