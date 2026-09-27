# Proof — slice 1, live multi-display viewer

Run: overnight 2026-09-26/27, cockpit Claude (session branch
`claude/distracted-hugle-89311e`). Device: Bobby's registered Pixel 10 Pro
Fold on USB, Android 17 (API 37), Cua runtime already deployed. All raw
evidence is git-ignored under `runs/phone-lab-runs/overnight-20260927/`
(cited below as `RUN/`) and `runs/phone-lab-runs/20260926/` (freezes). No
image is committed; display-0 captures are personal and stay local.

## Verdict

Working prototype. Every acceptance item in
`plans/slice-1-live-viewer.md` is proven on the Fold except one number: the
inner panel (display 0) captured at 0.25 fps on the wallpaper home screen,
below the 0.8 fps gate in the runbook. The Cua agent display met every gate.

## What was delegated versus written by the cockpit

- Job B (capture, server, UI, cua wrapper, CLI, tests): delegated once to
  Gemini 3.8 Flash via `antigravity-acp` (job
  `ecf6b0ba-93a7-4b5f-afeb-3010be2bb484`); it failed after 68 read-only
  tool calls with `PERMISSION_PROMPT_UNAVAILABLE` because the bridge ran
  `approve-reads`: plugin 0.4.0's `plugin.json` `mcpServers` block carries
  no `env`, so the `.mcp.json` break-glass never reached the broker. Filed
  as https://github.com/saariuslystoned/SaariusSkills/issues/92 on Bobby's
  request. **Fallback: the cockpit wrote Job B.** Bridge proof:
  `~/.local/state/saarius-skills/antigravity-acp-delegation/runs/ecf6b0ba-93a7-4b5f-afeb-3010be2bb484/PROOF.md`.
- Job C (feature map, verification skill, README): **written by the
  cockpit**; the lane was not retried because a merged fix cannot reach an
  already-running MCP server.
- Spec appendix on the real `cua-driver` argv and reply shapes: cockpit,
  measured from one probe session (`RUN/cua-json-shapes.txt`).

## Automated tests

`python3 -m unittest discover -s tests -v` → `Ran 22 tests … OK`
(5 display parsers, 4 registry, 5 compose/freeze, 7 cua wrapper and fixture
parsing, 1 capture-manager churn test). No device needed.

## Device proof (phase 3)

- Inventory (`RUN/inventory-before-demo.json`): Inner Display sf
  4619827677550801152 ↔ logical 0, 2076x2152, ON, status_bar_px 160; Outer
  Display ↔ logical 3, 1080x2364, OFF, status_bar_px 159;
  `studio.screen.sharing:0` ↔ logical 97, role ignored; no agent.
- Run 1 (23:16:41–23:31:44 local, `RUN/demo-run1.log`, `RUN/serve-run1.log`):
  session 519b2030 on logical display 100; 85/85 increment taps `ok`,
  fixture counter 0 → 85, 0 stale-frame retries, clean stop
  (`cleanup released`), registry record `stopped`.
- Run 2 (23:31:48 onward, `RUN/demo.log`, `RUN/serve.log`, session
  4c1a134a on logical display 101), sampled every 5 s for 2 minutes on the
  fixed build (`RUN/state-samples-2min-run2.jsonl`, `-summary.json`):

  | Panel | fps min / median / max | capture_ms median | notes |
  |---|---|---|---|
  | Inner Display (logical 0) | 0.24 / 0.25 / 0.26 | 4066 | 4 MB PNG per frame; FAILS 0.8 gate |
  | Outer Display (logical 3) | – | – | OFF, placeholder, never captured |
  | Cua agent (logical 101) | 1.38 / 1.63 / 2.00 | 510 | 242 captures, 0 seq resets across 12 SurfaceFlinger id changes |

  Agent session in `/api/state`: label `phone-lab demo`, package
  `ai.cua.fixture.notes`, lease min 49423 ms (never 0), 12/12 distinct
  taps `ok`, 0 stale retries.
- Freeze (`RUN/freeze-response.json`, `RUN/freeze-validation.txt`,
  `RUN/freeze-proof.json`, image `runs/phone-lab-runs/20260926/freeze-233452.png`):
  13/13 checks pass — schema `phone-lab.freeze.v1`, 3 panels, 64-hex
  sha256 on both captured panels, inner `cropped_status_bar_px` 160, OFF
  panel `seq null`, agent panel carries the session, composite 2190x1232
  (1.15 MB). Cockpit inspection of the PNG: three labelled panels, status
  bar absent from the inner panel, fixture visible at count 17, footer
  `phone-lab freeze · Pixel 10 Pro Fold · Android 17 · <timestamp>`.
- Viewer in the built-in browser (`RUN/viewer-run2-page.txt`): header
  with model and Android version, green connection dot, Inner Display live,
  Outer Display "display off", Cua agent panel with the fixture and the
  strip `Cua phone-lab demo · ai.cua.fixture.notes · lease 52 s · last tap
  increment ok · 6 s ago`. Browser screenshots were viewed by the cockpit
  only; the browser tool cannot save them to disk, and they show the
  personal home screen, so none is stored.
- Verification skill (`RUN/verify-verdict.json`,
  `skills/phone-lab-verify/scripts/verify.py --duration 120`): `result:
  fail`, 15/16 checks pass; the failing check is `display 0 fps >= 0.8`
  (median 0.25). Serial grep over the checkout (30 files) and the run
  manifests: 0 hits.

## Soak (phase 4)

Viewer restarted on the final server code at 23:36:20 local; sampler every
5 s for 30 minutes (`RUN/soak-30min.jsonl`, `-summary.json`,
`-analysis.txt`); demo session 4c1a134a kept running throughout.

| Measure | Result |
|---|---|
| Samples / transport errors | 360 / 0 over 30.0 min |
| Viewer process | alive for the whole soak, 7522 requests logged, no exception lines in `RUN/serve.log` |
| Capture threads | alive: inner captures 149 → 447, Cua captures 989 → 2982, no seq resets |
| Inner Display fps (min / median / max) | 0.23 / 0.25 / 0.64; capture_ms median 4044 (min 919, max 5592); 0 capture errors |
| Cua agent fps (min / median / max) | 1.30 / 1.71 / 2.35; capture_ms median 504; 171 transient "screencap returned no PNG" (one per Cua snapshot, 171 SurfaceFlinger id changes) |
| Taps during the soak | 171 distinct, 171 `ok`, 0 stale-frame retries |
| Lease | min 48964 ms, max 59579 ms, never 0 |
| Registry writes observed | 360 distinct `updated_at` values (one per sample) |
| Re-discovery | 851 inventory passes, 0 inventory errors |

Soak gates: no viewer crash ✓, no capture thread death ✓, fps median ≥ 0.8
✓ for the Cua display, ✗ for display 0 (0.25).

Whole run 2 (`RUN/demo.log`, 23:31:48–00:07:24): 200/200 increment taps
`ok`, 0 retries, 199 lease renewals; fixture counter read 202 at the end
(two more increments than logged taps; not investigated).

## Stop and residue (phase 6)

`RUN/stop-check.txt`: SIGTERM to the demo at 00:07:23; demo process exited
after 1.0 s ("interrupted; stopping the session" → "session 4c1a134a
stopped · cleanup released"); the agent panel was gone from `/api/state`
after 1.0 s (limit 10 s); registry records 4c1a134a and 519b2030 both
`state: stopped`, last action `stop ok cleanup released`. SurfaceFlinger
afterwards lists only the two physical panels and Android Studio's
`studio.screen.sharing:0`. The viewer was then stopped (serve.log ends with
a normal request line; process gone). `/data/local/tmp/cua-driver` on the
phone holds exactly the pre-run baseline (`cua-driver`, `runtime.apk`,
`runtime.log`, `runtime.pid`, five `smoke-*.png` from 16:34–19:21 the day
before); this run added nothing. No phonelab process left on the Mac.

## Defects found and fixed during proof

1. Cua surface swaps change the SurfaceFlinger id (`RUN/state-samples-2min.jsonl`,
   first run): each `snapshot` replaced the virtual display surface,
   SurfaceFlinger issued a new id, the old id failed ("Display Id … is not
   valid"), and threads keyed on sf id restarted every ~10 s. Fixed in
   commit ac8dd2c (threads keyed on uniqueId, failure wakes re-inventory,
   frame lookup by sf id / uniqueId / `logical-<n>`), regression test
   `tests/test_capture.py`.
2. `DisplayCapture._stop` shadowed `Thread._stop()` and broke `join()`.
   Same commit.
3. Agent session line clipped in the composite; session strip flickered in
   the viewer. Commit 294c661 (two-line strip with fit-to-width, title band
   150 px; strip rebuilt only when the session identity changes).
4. `/api/freezes` ordered same-second freezes wrongly. Fixed in review
   before the Job B commit.

## Measurements behind the display-0 limitation

`RUN/screencap-raw-vs-png.txt`, `RUN/transport-spike-2.txt`, uncontended:
inner `screencap -p` 3.0–3.7 s (4.06 MB PNG of the wallpaper home screen;
the ADR's 0.9 s was a ~240 KB PNG of flat content); raw `screencap`
2.2–2.5 s (17.9 MB); `screencap | gzip -1` on the phone 1.9–3.3 s (5.6 MB
on the wire); adb link 12.2 MB/s; host decode, resize and JPEG under
120 ms. A `screenrecord --output-format=raw-frames` trial did not honour
its time limit, created a `ScreenRecorder` virtual display and starved the
demo's adb calls; it was killed and not adopted. Decision: keep the spec's
PNG path and report the measured rate; options for Bobby are in
`docs/feature-map.md` (Known limits).

## Skipped or not done

- Display 0 at ≥ 0.8 fps: not met (see above). Roadmap box 1 left unticked.
- Sanitized public proof images in `saari-co/public-oss-proof-assets`: not
  produced (every composite contains the personal home screen; cropping
  the status bar is not enough to publish it).
- Gemini delegation: no job completed; both jobs written by the cockpit.

## Environment notes

- A second adb device (an Android emulator, `sdk_gphone64_arm64`) appeared
  at ~23:20 local. Not started or touched by the cockpit; every phonelab
  command afterwards pinned `--serial`. `verify.py` accepts `--serial` /
  `ANDROID_SERIAL` for the same reason.
- Keyguard was off all night; no swipe was needed.
- The Android Studio mirror display (`studio.screen.sharing:0`) was present
  throughout and correctly ignored.
