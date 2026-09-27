# Spike: streaming the human display (display 0) instead of polling screencap

Date: 2026-09-27. Device: Pixel 10 Pro XL (Android 17, 1080x2404 panel,
one physical display, no Cua runtime). Evidence:
`runs/phone-lab-runs/pixel-10-pro-xl/spike-display0-20260927/`
(`events.jsonl`, `spike.py`, `midstream.py`, `midstream-inventory.json`,
`STATE.md`, `PROOF.md`; git-ignored, no serial in any line). Host decoder:
ffmpeg from Homebrew (already installed; nothing was installed for this
spike; Python has no cv2 or PyAV).

## Question

Slice 1 measured `screencap -p` of the human panel at 3–4 s on busy content
and no screencap transport under 1.9 s; the display-0 gate of 0.8 fps was
missed. An overnight `screenrecord --output-format=raw-frames` trial ignored
its time limit, left a `ScreenRecorder` virtual display and starved every
other adb call. This spike measures, safely and bounded (≤ 25 s per trial,
hard client-side kill, device checked clean after every trial), whether
`screenrecord` streamed to the host can reach 0.8 fps without those side
effects.

## Method

Every trial pins the XL by model. After each streaming trial the script
checks `pidof screenrecord` and `dumpsys SurfaceFlinger --display-id` for a
`ScreenRecorder` display. While streaming, `adb shell echo` runs once a
second to measure contention. Because `screenrecord` only emits frames when
the screen changes, five timed swipes on the XL's home screen (starting at
t = 3 s) provide motion; latency is measured from the swipe command's issue
time to the first decoded frame that differs from the previous one (an
upper bound that includes the `adb shell input` round trip, ~100 ms).
Streams are teed to ffmpeg live (`-fflags nobuffer -flags low_delay
-probesize 32 -analyzeduration 0`) and the same bytes are counted offline.
Stream files were deleted after measurement (personal screen content).

## Numbers

Baseline, `adb exec-out screencap` on display 0 (1080x2404, home screen):

| Path | Bytes | Trial 1 | Trial 2 | Trial 3 |
|---|---|---|---|---|
| `screencap -p` (PNG) | 2.21 MB | 2633 ms | 2649 ms | 6905 ms |
| `screencap` (raw RGBA) | 10.39 MB | 1495 ms | 1510 ms | 1511 ms |

Polling PNG therefore gives 0.15–0.38 fps on this panel; raw 0.66 fps.

`screenrecord --output-format=h264 --size 1080x2404 --bit-rate 4000000
--time-limit 10 -`, three trials each, static screen then with swipes:

| Metric | Static screen (3 trials) | With motion (3 trials) |
|---|---|---|
| Bytes in 10 s | 15.7–15.8 KB | 2.77–2.98 MB (0.25–0.27 MB/s) |
| Encoded frames (offline ffmpeg count) | 1–2 | 573–605 (57–61 fps) |
| Frames decoded live on the host | 1–2 | 333–365 (30–33 fps over the stream, 45–50 fps in the motion window) |
| First byte on the host | 10.8–11.2 s (nothing to send until exit) | 0.57–0.60 s |
| First decoded frame | – | 6.5–6.6 s (3.5 s after the first swipe: ffmpeg's raw-h264 probe needs several frames) |
| Latency, swipe command → changed frame, steady state | – | 0.25–0.66 s (median ≈ 0.35 s); the first two stimuli see 2.1–3.6 s while the decoder primes |
| `--time-limit 10` honoured | yes, exit 0 at 11.0–12.0 s | yes, exit 0 at 11.0–12.1 s |
| `pidof screenrecord` after exit | empty, all 6 | empty, all 6 |
| `ScreenRecorder` virtual display after exit | none, all 6 | none, all 6 |
| Concurrent `adb shell echo` while streaming | 36–152 ms, all under 1 s | 35–150 ms, all under 1 s |
| Host decode of a 10 s segment (offline) | – | 186–488 ms |

`screenrecord --output-format=raw-frames --size 270x601 --time-limit 10 -`
(quarter size, RGBA, 649 KB per frame), three trials, stdout read
continuously with `read1` and swipes as above:

| Metric | Trials 1–3 |
|---|---|
| `--time-limit 10` honoured | **no**: still streaming at 25 s, hard-killed all three times |
| Bytes at kill | 62.8–66.2 MB (2.5–2.6 MB/s) |
| Frames at kill | 96–102 (only on motion; 13 fps in the motion window, 9–15 s gaps when static) |
| Latency, swipe → changed frame | 0.28–0.48 s |
| Concurrent `adb shell echo` | 40–146 ms, all under 1 s |
| `pidof screenrecord` after the host kill | **still present** all three times; gone after `pkill -INT screenrecord` |
| `ScreenRecorder` virtual display after the host kill | none |

Inventory while an h264 stream runs (`midstream-inventory.json`):
`dumpsys SurfaceFlinger --display-id` shows a second entry, `Display
11529215046129214854 (Virtual display): displayName="ScreenRecorder"
uniqueId=""`; `dumpsys display` shows nothing for it. phone-lab's
`inventory()` lists it as `kind: virtual, role: ignored, unique_id: ""`,
so no capture thread starts for it, and it is gone at the next inventory
after the stream ends (displays before/during/after: 1/2/1).

## Answers

1. **Best case display refresh.** h264 streaming delivers every changed
   frame at the panel's rate (≈ 60 fps encoded, 30–50 fps decoded live on
   the host in Python without trying) at 0.25–0.3 MB/s, against a 0.8 fps
   gate that polling misses by 2–5×. Steady-state latency is about 0.35 s
   from input to host frame. On a static screen nothing is sent, which is
   the right behaviour for a viewer (keep the last frame, show its age).
2. **Side effects.** With `--output-format=h264` the process honours
   `--time-limit`, exits 0, leaves no process and no virtual display, and
   concurrent adb calls stay at 40–150 ms. The overnight failure is
   specific to `raw-frames`: it ignores `--time-limit` even with a consumer
   that never stalls, and killing the host side leaves the device-side
   process alive until `pkill -INT screenrecord`. Raw-frames is not
   adoptable.
3. **Decode feasibility.** ffmpeg is present on this host and decodes the
   raw Annex-B stream live; the only pitfalls found were host-side (a
   buffered 64 KB `read()` on the pipe, and ffmpeg's probe delay before
   the first frame). No Python-only decoder is installed, so ffmpeg on
   PATH is a prerequisite of the streaming path and it must stay opt-in.

## What is adoptable, and what it changes in capture.py

- A per-display **frame source** abstraction: `DisplayCapture` asks a
  source for the next frame instead of calling `adb.screencap` itself.
  `ScreencapSource` keeps today's behaviour byte for byte;
  `H264StreamSource` (new `phonelab/stream.py`) runs one `screenrecord`
  segment at a time (`--time-limit 180`, restarted when it exits), pumps
  its stdout into ffmpeg with `read1`, keeps only the latest decoded frame,
  rate-caps delivery (default 5 fps) and encodes PNG plus JPEG with Pillow.
  `CaptureManager` takes a `source_factory(display)`; `serve --stream-human`
  (default off) supplies it for the human display with logical id 0 only.
- Stream frames are downscaled (`--max-height`), unlike screencap frames,
  so a freeze taken from a streamed panel is a downscaled PNG, its manifest
  reports the decoded size (964x1000 on the Fold), and the status-bar crop
  (sized for the full panel) over-crops it. Both need a follow-up before
  streaming could be the default.
- `stop()` on the stream source always runs `pkill -INT screenrecord` and
  checks `pidof` afterwards, the cleanup that the raw-frames trials showed
  is necessary after a host-side kill.

## Risks

- **Virtual display in the inventory.** While a stream runs SurfaceFlinger
  lists a `ScreenRecorder` virtual display with an empty `uniqueId`. The
  uniqueId join is unaffected (Cua displays have non-empty ids, and the
  entry is `role: ignored`), but anything that keys on an empty uniqueId,
  or that counts virtual displays, would see one extra entry. Cua's own
  snapshot path was not exercised in this spike (the XL has no Cua
  runtime); that check belongs to the device proof on the Fold.
- **adb contention.** At 0.3 MB/s the stream is far under the 12 MB/s link
  and probes stayed under 150 ms; raw-frames at 2.6 MB/s also stayed under
  150 ms. A busier screen (video) raises the h264 rate toward the bit-rate
  cap (4 Mbit/s = 0.5 MB/s), still small.
- **Fold posture.** `screenrecord` records the default display (logical
  0), which on the Fold is whichever panel is active; a posture change
  makes the source's `--size` wrong and the segment restart picks up the
  new inventory. Not measured here.
- **Latency on a static screen.** The first frame after a long idle
  arrives once ffmpeg has enough data to prime; the measured 2–3.6 s
  applied to the first stimulus only. A persistent stream primes once.
- **Only ffmpeg.** No decoder in the Python environment; the flag refuses
  to start without ffmpeg on PATH.
- **Personal screen.** A stream of display 0 is the human's screen at 60
  fps; nothing leaves `runs/`, and the spike deleted its stream files.

## Fold proof (same evening)

`runs/phone-lab-runs/pixel-10-pro-fold/spike-display0-fold-20260927/PROOF.md`.
Open posture, inner panel 2076x2152 as logical 0. `serve --stream-human`
with a concurrent `cua demo`: the Cua display kept capturing at 1.2–1.7
fps, its session stayed `active`, 45 s of taps passed, the freeze wrote
three panels with the human one from the stream, and after SIGTERM no
process or `ScreenRecorder` display remained. Instrumented h264 trials on
the inner panel: 34 fps encoded at 0.2 MB/s, 0.29–0.65 s latency once
primed, time limit honoured, clean. Two limits found: the first frame
after an idle period surfaces about 5 s after motion starts (on both
phones; ffmpeg `-threads 1` does not change it), so a short burst of
motion may not appear until more motion follows; and the streamed freeze
manifest reports the decoded size with an over-sized status-bar crop.
Posture change was not exercised.

## Recommendation for Bobby

**Adopt, opt-in.** The h264 path clears the 0.8 fps gate by two orders of
magnitude with no observed side effects on the XL, and the implementation
is confined to a source abstraction plus one new module behind
`--stream-human`. Keep screencap as the default until the Fold proof (Cua
session running, a posture change, and one freeze from a streamed panel)
passes; then decide whether streaming becomes the default for human
panels. Drop raw-frames entirely. Fold proof (below) passed the Cua and
freeze checks; the posture change and the two freeze follow-ups remain.
