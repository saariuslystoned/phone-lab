# Slice 1 — Live multi-display viewer (spec)

Date: 2026-09-26. Stack per [ADR 001](../docs/adr-001-tech-stack.md).
Python 3.12, standard library plus Pillow. No other dependencies.

## Package layout

```
phonelab/
  __init__.py
  __main__.py     CLI: inventory | serve | cua demo
  adb.py          Adb wrapper, device selection, serial redaction
  displays.py     dumpsys parsers and the Display model
  sessions.py     on-disk session registry
  capture.py      per-display capture threads and the capture manager
  server.py       HTTP server, JSON API, freeze-frame composites
  cua.py          cua-driver wrapper and the fixture demo loop
  ui/index.html   the viewer
tests/
  fixtures/       captured dumpsys text (no serials)
  test_displays.py, test_sessions.py
```

Run tests with `python3 -m unittest discover -s tests -v`.

## Privacy rules that the code enforces

- The device serial is used only in the `adb -s` and `cua-driver --device`
  argument lists. `Adb.redact(text)` replaces it in every error message,
  log line, and JSON payload. The UI and manifests show the model name only.
- Raw captures and freezes are written only under the runs directory
  (default `runs/phone-lab-runs/`, git-ignored).
- Composites crop `status_bar_px` from the top of physical panels.

## `phonelab/adb.py`

```python
class AdbError(Exception): ...

class Adb:
    def __init__(self, serial: str | None = None, adb: str = "adb"): ...
    def resolve(self) -> "Adb"        # picks the single authorized device
    serial: str; model: str            # model from `adb devices -l` (Pixel_10_Pro_Fold -> "Pixel 10 Pro Fold")
    def redact(self, text: str) -> str
    def shell(self, *args: str, timeout: float = 15) -> str
    def exec_out(self, *args: str, timeout: float = 30) -> bytes
    def screencap(self, sf_display_id: str, timeout: float = 30) -> bytes | None
    def props(self) -> dict            # {"android_release": "17", "api_level": 37}
```

`resolve()` parses `adb devices -l`; only lines whose state is `device`
count. Zero devices or more than one without an explicit serial raise
`AdbError` (message must not contain any serial). `screencap` returns the
bytes only when they start with the PNG signature, otherwise `None`
(SurfaceFlinger prints "Failed to take take screenshot..." for a stale id).

## `phonelab/displays.py`

```python
@dataclass
class Display:
    sf_id: str                 # SurfaceFlinger id, used by screencap -d
    unique_id: str             # "local:<id>" or "virtual:<owner>,<uid>,<name>,<n>"
    name: str                  # "Inner Display", "Outer Display", "Cua agent", ...
    kind: str                  # "physical" | "virtual"
    logical_id: int | None     # from dumpsys display; None if not joined
    width: int | None
    height: int | None
    state: str                 # "ON" | "OFF" | "UNKNOWN"
    owner: str | None          # e.g. "com.android.shell" for virtual displays
    status_bar_px: int         # cutout inset top for physical panels, else 0
    role: str                  # "human" | "agent" | "ignored"

def parse_surfaceflinger(text: str) -> list[dict]
def parse_dumpsys_display(text: str) -> dict[str, dict]   # keyed by unique_id
def join(sf: list[dict], dd: dict[str, dict]) -> list[Display]
def inventory(adb: Adb) -> list[Display]
def to_json(d: Display) -> dict
```

`parse_surfaceflinger` reads lines of the two forms in
`tests/fixtures/surfaceflinger_display_id_fold_live.txt`:

```
Display 4619827677550801152 (HWC display 0): port=0 pnpId=GGL screenPartStatus=ORIGINAL displayName="Common Panel"
Display 11529215047354549223 (Virtual display): displayName="Cua agent" uniqueId="virtual:com.android.shell,2000,Cua agent,89"
```

Physical entries get `unique_id = "local:" + sf_id` and `kind="physical"`.

`parse_dumpsys_display` reads `tests/fixtures/dumpsys_display_fold_live_excerpt.txt`
(an excerpt of the real output; the parser must also cope with the full
2000-line dump). From the `Display Devices` section it takes, per
`DisplayDeviceInfo{"<name>": uniqueId="<uid>", <W> x <H>, ...}`, the name,
size, `state <ON|OFF>`, `owner <pkg>` when present, and the cutout top inset
from `cutout DisplayCutout{insets=Rect(0, <top> - 0, 0)` when present. From
the `Logical Displays` section it takes, per `mDisplayId=<n>` followed by
`mBaseDisplayInfo=DisplayInfo{"<name>", displayId <n>, ... uniqueId "<uid>"`,
the logical id. Later `mDisplayId=` lines outside that section (there are
many) must not confuse the parser: stop at the line `Display Devices` or at
the end of the `Logical Displays: size=N` block (N entries).

`join` matches on `unique_id`. Roles: `physical` → `human`; virtual with
`name == "Cua agent"` → `agent`; any other virtual → `ignored`. Order:
human displays by logical id, then agents by logical id, then ignored.

Expected from the fixtures: four displays; inner panel sf
`4619827677550801152` ↔ logical 0, 2076x2152, ON, status_bar_px 160; outer
panel `...153` ↔ logical 3, 1080x2364, OFF, status_bar_px 159; `Cua agent`
sf `11529215047354549223` ↔ logical 98, 1080x1920, role agent, owner
`com.android.shell`; `studio.screen.sharing:0` ↔ logical 97, role ignored.

## `phonelab/sessions.py`

Registry directory: `<runs_dir>/sessions/`. One JSON file per session,
`<session_id>.json`, written atomically (write `.tmp`, then `os.replace`).

```python
@dataclass
class SessionRecord:
    session_id: str
    label: str | None
    display_id: int | None        # logical display id from cua-driver
    package: str | None
    target_id: str | None
    state: str                    # "active" | "stopped" | "lost"
    lease_remaining_ms: int
    lease_checked_at: float       # time.time() when lease_remaining_ms was read
    last_action: dict | None      # {"kind": str, "at": float, "result": str, "detail": dict}
    owner: str                    # e.g. "phonelab cua demo"
    updated_at: float
    def lease_remaining_now(self, now: float | None = None) -> int   # clamps at 0
    def to_json(self) -> dict

class Registry:
    def __init__(self, runs_dir: Path)
    def write(self, rec: SessionRecord) -> Path
    def load_all(self) -> list[SessionRecord]          # skips corrupt files, never raises on them
    def by_display(self) -> dict[int, SessionRecord]   # active records only, latest updated_at wins
```

## `phonelab/capture.py`

```python
@dataclass
class Frame:
    seq: int; captured_at: float; capture_ms: int
    png: bytes; jpeg: bytes; width: int; height: int

class DisplayCapture(threading.Thread):   # one per display
    # loop: skip (sleep 1 s, error="display off") while display.state == "OFF";
    # else adb.screencap(sf_id); decode with Pillow; downscale to max_height (default 1000)
    # JPEG quality 80; store latest Frame; keep an EMA fps over the last 10 captures;
    # record the last error string when screencap returns None or raises.
    # min interval between captures: `interval` seconds (default 0.0; capture time dominates)

class CaptureManager:
    def __init__(self, adb: Adb, registry: Registry, rediscover_every: float = 2.0, max_height: int = 1000)
    def start(self) / stop(self)
    # rediscovery thread: displays.inventory(adb) every rediscover_every seconds;
    # start a DisplayCapture for new sf_ids with role != "ignored"; stop threads whose display vanished;
    # update each thread's Display (state can flip ON/OFF).
    def state(self) -> dict          # see /api/state below
    def frame(self, sf_id: str) -> Frame | None
    def frames(self) -> list[tuple[Display, Frame | None]]   # in display order
```

## `phonelab/server.py`

`ThreadingHTTPServer` on `127.0.0.1:8791` by default.

- `GET /` → `ui/index.html`.
- `GET /api/state` →

```json
{"device": {"model": "Pixel 10 Pro Fold", "android_release": "17", "api_level": 37},
 "server_time": 1790000000.0,
 "runs_dir": "runs/phone-lab-runs",
 "displays": [{"sf_id": "...", "logical_id": 0, "name": "Inner Display", "kind": "physical",
               "role": "human", "state": "ON", "width": 2076, "height": 2152,
               "seq": 41, "captured_at": 1790000000.0, "capture_ms": 905, "fps": 1.1,
               "error": null,
               "session": null}]}
```

  For an agent display with a registry match, `session` is the
  `SessionRecord.to_json()` plus `"lease_remaining_now_ms"`; for an agent
  display without one, `session` is `{"state": "unknown"}`.
- `GET /frame/<sf_id>.jpg` → latest JPEG, `Cache-Control: no-store`,
  `X-Frame-Seq: <seq>`; 404 when no frame yet.
- `POST /api/freeze` → builds the composite and returns
  `{"image": "<path>", "manifest": "<path>", "panels": N}`.
- `GET /api/freezes` → the ten most recent freeze manifests (parsed JSON).

Composite: dark canvas (18,18,22); one panel per display in display order
(skip `ignored`; render OFF or frameless displays as a grey placeholder with
the label). Physical panels are cropped by `status_bar_px` from the top.
All panels scaled to height 1000, 24 px gutters, 120 px title band. Title
line per panel (font size 30): `{name} · logical {logical_id} · {W}x{H}`;
second line (size 22): `seq {seq} · captured {HH:MM:SS.mmm} · {capture_ms} ms · {fps:.1f} fps`.
Agent panels get a third line (size 22, blue): `Cua {label or session_id[:8]} ·
{package} · lease {lease_remaining_now_ms/1000:.0f}s · last {kind} {result}`
or `Cua session: unknown to phone-lab`. Footer (size 22, grey): `phone-lab
freeze · {model} · Android {release} · {ISO timestamp}`. Use
`ImageFont.load_default(size=...)` (Pillow ≥ 10.1).

Files: `<runs_dir>/<YYYYMMDD>/freeze-<HHMMSS>.png` and `.json`. Manifest:

```json
{"schema": "phone-lab.freeze.v1", "created_at": "...", "device": {...},
 "image": "freeze-HHMMSS.png",
 "panels": [{"sf_id": "...", "logical_id": 0, "name": "...", "role": "human",
             "seq": 41, "captured_at": 1790000000.0, "capture_ms": 905,
             "width": 2076, "height": 2152, "png_sha256": "...", "cropped_status_bar_px": 160,
             "session": null}]}
```

## `phonelab/ui/index.html`

Vanilla HTML/CSS/JS, dark theme, no external resources.

- Header: "phone-lab", device model and Android version, connection dot
  (green when `/api/state` answered within 3 s, red otherwise), a
  "Freeze frame" button, and the last freeze path as a toast.
- Grid of panels, one per non-ignored display, in server order. Panel
  header: name, `logical <id>`, role badge (human grey, agent blue). Image:
  `<img>` whose `src` is set to `/frame/<sf_id>.jpg?seq=<seq>` only when
  `seq` changes (poll `/api/state` every 500 ms). Footer: `WxH · seq · fps ·
  age` where age is `now - captured_at`, ticking client-side.
- Agent panels show a session strip: label, package, lease countdown
  (client-side decrement from `lease_remaining_now_ms` since the last
  poll, floor 0, red under 10 s), last action kind + result + age. Unknown
  session → "session unknown to phone-lab".
- OFF displays show a placeholder ("display off") instead of an image;
  errors show the error text under the panel.

## `phonelab/cua.py`

```python
class CuaDriver:
    def __init__(self, adb: Adb, binary: Path)
    def call(self, *args: str, session: str | None = None, timeout: float = 30) -> dict
        # runs: <binary> --device <serial> [--session <sid>] <args...>; parses JSON; drops data.image_base64;
        # raises CuaError(status, reason) when exit_code != 0 (message redacted)
    def create(self, allow_apps: list[str], label: str) -> dict
    def launch(self, sid, package) -> dict
    def inspect(self, sid) -> dict
    def renew(self, sid) -> dict
    def snapshot(self, sid, target) -> dict
    def tap(self, sid, snapshot_id, x, y) -> dict
    def stop(self, sid) -> dict

def fixture_state(adb: Adb, package="ai.cua.fixture.notes") -> dict
    # adb shell content query --uri content://<package>.state ; JSON after "json="

def demo(adb, driver: CuaDriver, registry: Registry, duration_s: int, tap_every_s: float, taps: bool) -> int
```

`demo`: create a session (`--allow-app ai.cua.fixture.notes --label
"phone-lab demo"`), launch the fixture, write a `SessionRecord`
(owner `phonelab cua demo`) with `display_id`, `package`, `target_id`, lease
from the create/launch replies, `last_action = {"kind": "launch", ...}`.
Loop until `duration_s` or Ctrl-C: renew every 10 s; when `taps` and
`tap_every_s` elapsed, read `fixture_state()["controls"]["increment"]`
(`{"x":..,"y":..}`) and `counter`, take a snapshot, tap at that point; a
`frame_stale` refusal is retried with a fresh snapshot up to three times
and counted; record `last_action = {"kind": "tap increment", "result":
"ok"|"refused:<reason>", "detail": {"counter_before": n, "frame_age_ms": ..}}`.
After each loop iteration call `inspect` and rewrite the record with the
fresh lease. On exit, `stop` the session and write `state="stopped"`.
Print one line per action to stdout (redacted). Return 0 on clean exit.

## `phonelab/__main__.py`

```
python3 -m phonelab inventory [--serial S]                 # prints displays JSON
python3 -m phonelab serve [--host 127.0.0.1] [--port 8791] [--serial S] [--runs-dir runs/phone-lab-runs] [--max-height 1000]
python3 -m phonelab cua demo --driver PATH [--duration 300] [--tap-every 8] [--no-taps] [--serial S] [--runs-dir ...]
```

`--driver` may also come from the environment variable `PHONELAB_CUA_DRIVER`.

## Acceptance (proof on the Fold)

1. `python3 -m unittest discover -s tests -v` passes.
2. `python3 -m phonelab inventory` lists the inner panel, cover panel, and,
   while a Cua session is live, the Cua display with role `agent`.
3. With `cua demo` running, the viewer shows display 0 and the Cua display
   live with fps ≥ 0.8 each, the Cua panel labelled with package, lease, and
   the last tap result, and a freeze produces a composite plus manifest.
