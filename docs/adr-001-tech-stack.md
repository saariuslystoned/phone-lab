# ADR 001: Local-first Python server plus a plain web UI

## Status

Accepted (2026-09-26). Bobby accepted the proposal on the evening of
2026-09-26; slice 1 was then built on it and proven on the Fold overnight
(see `proof/slice-1-live-viewer/PROOF.md`). One measured correction to the
context table below: the inner-panel `screencap -p` cost of ~0.9 s held for
flat content (a ~240 KB PNG); on the wallpaper home screen the PNG is 4 MB
and the capture takes 3–4 s, and no screencap transport measured overnight
(raw, raw+gzip) got it under 1.9 s over the 12 MB/s adb link. The 1–2 fps
ceiling in Consequences therefore applies to the agent display; display 0
can be slower, and the viewer shows the measured rate.

## Context

phone-lab needs to watch several Android displays at once (the human's inner
panel, the powered-off cover panel, and every Cua virtual display), label
them with agent session state, freeze labelled composites into proof, and
later record trails, render traces, and replay with self-heal. It is a
personal lab that must run on Bobby's Mac against a USB phone with nothing
to deploy and nothing to sign in to.

Facts measured on the Pixel 10 Pro Fold (Android 17) on 2026-09-26:

| Capture path | Cost |
|---|---|
| `adb exec-out screencap -p -d <inner panel>` (2076x2152 PNG, ~240 KB) | ~0.9 s |
| `adb exec-out screencap -p -d <Cua display>` (1080x1920 PNG, ~36 KB) | ~0.65 s |
| `cua-driver session inspect` | fast, but there is no session-list command |
| ADB content-provider read of the fixture oracle | ~1.3 s |

Every proven helper from the 2026-09-26 evidence campaign (probe helpers,
dual-display composites, frame-age and fold probes, the UiAutomation spike
runner) is Python with Pillow. Pillow 12 is already installed for the
interpreter on PATH.

## Decision

1. **Language and runtime**: Python 3.12, standard library plus Pillow.
   No web framework, no async runtime, no package manager step. One
   thread per captured display; `ThreadingHTTPServer` serves the UI.
2. **UI**: a single vanilla `index.html` served from the same process and
   opened in the Claude app's built-in browser or any browser. No build
   step, no node_modules. Frames are polled as JPEG (downscaled) and swapped
   only when the server-side frame sequence changes; state is polled as
   JSON at 2 Hz.
3. **Display model**: SurfaceFlinger ids (for `screencap -d`) and logical
   display ids (for `input -d`, Cua `display_id`) are joined on the display
   `uniqueId` that both `dumpsys SurfaceFlinger --display-id` and
   `dumpsys display` print. Physical panels are the human's; virtual
   displays named `Cua agent` are agent displays; other virtual displays
   (Android Studio mirroring) are ignored.
4. **Session state**: Cua exposes no session list, so phone-lab owns a
   session registry on disk (`runs/phone-lab-runs/sessions/*.json`). Anything
   that creates or drives a Cua session through phone-lab writes its label,
   package, lease, and last action there; the viewer joins the registry to
   displays on the logical display id. Foreign Cua displays still show, with
   an "unknown session" badge.
5. **Proof**: raw captures and freeze-frames go to git-ignored
   `runs/phone-lab-runs/<date>/`; each freeze writes a JSON manifest with
   sizes, sequence numbers, capture times, and SHA-256 digests. The status
   bar is cropped from human panels in composites. Tracked proof under
   `proof/` is text only.

## Alternatives considered

- **Rust CLI beside cua-driver**: fastest capture path later (a
  `screenrecord`/H.264 stream), but slows the first four slices and there
  is no reusable Rust from today's evidence. Revisit only when 1–2 fps
  polling is measurably the bottleneck.
- **`screenrecord` H.264 stream for the human panel** (measured
  2026-09-27 on the Pixel 10 Pro XL, `docs/spike-display0-streaming.md`):
  `screenrecord --output-format=h264 -` streamed over adb and decoded by
  ffmpeg delivers every changed frame (≈ 60 fps encoded, 30–50 fps decoded
  on the host) at 0.3 MB/s with ≈ 0.35 s latency, honours `--time-limit`,
  leaves no process or virtual display behind, and keeps concurrent adb
  calls under 150 ms; polling screencap gives 0.15–0.66 fps on the same
  panel. `raw-frames` ignores its time limit and is dropped. Adopted as an
  opt-in `serve --stream-human` source (needs ffmpeg on PATH) behind the
  same capture threads; screencap stays the default until the Fold proof.
- **Kotlin Compose desktop app** (the Trailblaze shape): a heavier toolchain
  for a one-person lab, and no browser-native trace viewer for free.
- **FastAPI/uvicorn plus a React trace viewer**: nicer streaming
  (WebSocket, MJPEG), but adds a virtualenv and a JS build. The stdlib
  server can grow an MJPEG endpoint without changing the UI contract; a
  framework is a one-file swap if it is ever needed.
- **Polling `cua-driver snapshot` instead of `screencap`**: the snapshot
  path is Cua's own actionable capture; it can return frames up to 4.2 s
  old and it consumes the session. Independent `screencap -d` on the
  virtual display is cheaper and does not touch the agent's snapshot
  handles.

## Consequences

- 1–2 fps per display is the honest ceiling over USB ADB with PNG
  screencap; the UI shows the measured rate per panel instead of
  pretending to be a mirror.
- Every capture is a subprocess; display discovery re-runs every two
  seconds so Cua displays appear and disappear with their sessions.
- Later slices (element refs, trails, trace viewer, self-heal) reuse the
  same server, registry, and runs layout: refs come from the UiAutomation
  tree read on any display, trails are JSON files under `runs/`, and the
  trace viewer is another static page served by the same process.
