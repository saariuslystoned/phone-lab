# Shared-machine instances (spec)

Date: 2026-09-27. Follows the device-selection work merged at `7202e70`.
Python 3.12, standard library only for everything here. No device is
needed for any test in this slice.

## Problem

Other agents run emulators, phones, and their own phone-lab instances on
the same machine. Device selection is done. Three things still collide:

1. Two viewers both default to port 8791 and the second one dies with a
   raw `OSError` from deep inside `socketserver`.
2. The session registry (`runs/phone-lab-runs/sessions/`) and the freeze
   directories are flat, so a viewer for one phone labels its Cua panel
   with a session that belongs to another phone.
3. Two viewers for the same device on the same runs directory write over
   each other silently.

## Decisions

- A **device tag** is derived from the model name only, never the serial:
  `device_tag("Pixel 10 Pro Fold") == "pixel-10-pro-fold"`. Rule: casefold,
  every run of characters outside `[a-z0-9]` becomes one `-`, leading and
  trailing `-` stripped, empty result becomes `unknown`. `--device-tag T`
  overrides it (two phones of the same model on one machine).
- `--runs-dir` keeps meaning the **runs root** (default `runs/phone-lab-runs`).
  Everything an instance writes goes under `<root>/<device-tag>/`, called
  the **device dir** below. Both `serve` and `cua demo` use this layout.
- Port conflicts fail fast with the port in the message, before any adb
  call. `--port 0` binds a free port and prints the real URL.
- Presence is per instance, not a mutex: a second viewer is **reported**
  on stdout and in `/api/state`, never refused.

## Path changes

```
before                                     after
runs/phone-lab-runs/sessions/<sid>.json    runs/phone-lab-runs/<tag>/sessions/<sid>.json
runs/phone-lab-runs/<YYYYMMDD>/freeze-*    runs/phone-lab-runs/<tag>/<YYYYMMDD>/freeze-*
(none)                                     runs/phone-lab-runs/<tag>/viewers/<pid>.json
```

Backwards compatibility: the registry still **reads** the old flat
`<root>/sessions/` directory. Old records have no `device_tag`; they are
accepted as this device's (they predate tagging, when only one phone was
attached). New records are written only to the device dir. Old freeze
directories are not migrated and are not listed by `/api/freezes`
(freeze listing is per device dir).

## JSON changes

`SessionRecord` gains one trailing field with a default, so old files still
load:

```json
{
  "session_id": "…", "label": "phone-lab demo", "display_id": 98,
  "package": "ai.cua.fixture.notes", "target_id": "…", "state": "active",
  "lease_remaining_ms": 60000, "lease_checked_at": 1.0,
  "last_action": {"kind": "tap increment", "at": 1.0, "result": "ok", "detail": {}},
  "owner": "phonelab cua demo", "updated_at": 1.0,
  "device_tag": "pixel-10-pro-fold"
}
```

`Registry.load_all()` drops a record whose `device_tag` is set and differs
from the registry's own tag. `by_display()` is unchanged on top of that.

Presence file `<device dir>/viewers/<pid>.json`, rewritten atomically
(tmp + `os.replace`) every 5 s from the server's own poll loop:

```json
{
  "schema": "phone-lab.viewer.v1",
  "pid": 4242,
  "host": "127.0.0.1",
  "port": 8791,
  "url": "http://127.0.0.1:8791/",
  "device_tag": "pixel-10-pro-fold",
  "started_at": 1790000000.0,
  "heartbeat_at": 1790000123.0
}
```

A presence file is **live** when its pid is not ours, `os.kill(pid, 0)`
succeeds (or raises `PermissionError`, which still means alive), and
`heartbeat_at` is less than 30 s old. Dead or stale files are deleted by
whoever notices them. A clean stop deletes our own file.

`GET /api/state` gains two keys and one field:

```json
{
  "device": {"model": "Pixel 10 Pro Fold", "device_tag": "pixel-10-pro-fold",
             "android_release": "17", "api_level": 37},
  "runs_dir": "runs/phone-lab-runs/pixel-10-pro-fold",
  "viewers": [{"pid": 4242, "url": "http://127.0.0.1:8792/", "heartbeat_age_s": 3.1}],
  "...": "unchanged"
}
```

`viewers` lists the *other* live viewers of this device dir; it is `[]`
when we are alone. Freeze manifests inherit the new `device.device_tag`
through the existing `device` dict; the schema id stays
`phone-lab.freeze.v1` because the field is additive.

## Module contracts

### `phonelab/adb.py`

```python
def device_tag(model: str) -> str: ...
```

`Adb.resolve()` sets `self.tag = device_tag(self.model)` after choosing the
device. Nothing else changes.

### `phonelab/sessions.py`

```python
@dataclass
class SessionRecord:
    ...  # existing fields unchanged, then:
    device_tag: str | None = None

class Registry:
    def __init__(self, runs_root: Path, device_tag: str | None = None) -> None:
        # device_tag None keeps today's flat behaviour (used by existing tests):
        #   dir = runs_root/sessions, legacy_dir = None
        # otherwise:
        #   dir = runs_root/device_tag/sessions, legacy_dir = runs_root/sessions
    def write(self, rec) -> Path      # stamps rec.device_tag = self.device_tag when rec.device_tag is None
    def load_all(self) -> list[SessionRecord]
        # reads dir then legacy_dir (if any); skips corrupt files as today;
        # drops records with a device_tag that is set and != self.device_tag
    def by_display(self) -> dict[int, SessionRecord]  # unchanged
```

`cua.demo` sets `device_tag=adb.tag` when it builds the record.

### `phonelab/presence.py` (new)

```python
SCHEMA = "phone-lab.viewer.v1"
HEARTBEAT_EVERY_S = 5.0
STALE_AFTER_S = 30.0

class ViewerPresence:
    def __init__(self, device_dir: Path, host: str, port: int, device_tag: str,
                 pid: int | None = None, clock=time.time, pid_alive=None) -> None
    def start(self) -> None          # write our file (started_at = heartbeat_at = now)
    def beat(self, force: bool = False) -> None   # rewrite when HEARTBEAT_EVERY_S has passed
    def others(self) -> list[dict]   # live foreign files as {"pid", "url", "heartbeat_age_s"}; prunes dead/stale
    def stop(self) -> None           # remove our file; missing file is not an error
```

`pid_alive(pid) -> bool` defaults to the `os.kill(pid, 0)` rule above and is
injectable so tests never depend on real processes.

### `phonelab/server.py`

```python
class PortInUse(OSError):
    """`host:port` already has a listener. The message names the port."""

def bind_viewer(host, port, adb, manager, device, runs_dir) -> ViewerServer
    # ThreadingHTTPServer bind; errno EADDRINUSE -> PortInUse(
    #   f"port {port} on {host} is already in use (another phone-lab viewer or another agent's server?); "
    #   f"pass --port 0 to pick a free port or --port N for another one")
    # port 0 is allowed; the real port is server.server_address[1]

def viewer_url(server) -> str        # f"http://{host}:{real_port}/"

class ViewerServer(ThreadingHTTPServer):
    presence: ViewerPresence | None
    def service_actions(self) -> None   # called by serve_forever every poll; calls presence.beat()

def serve(adb, registry, host, port, runs_dir, max_height) -> int
    # order: bind_viewer FIRST (fail fast, no adb call yet) -> adb.props() -> CaptureManager.start()
    #        -> presence.start(); print(others) -> banner with the real URL -> serve_forever
    # PortInUse: print(f"error: {exc}", file=sys.stderr); return 2
    # finally: presence.stop(), server_close, manager.stop
```

`/api/state` adds `"viewers": presence.others()`; `device` carries
`device_tag`. The banner line becomes:

```
phone-lab viewer on http://127.0.0.1:8792/ · Pixel 10 Pro Fold · Android 17 · runs runs/phone-lab-runs/pixel-10-pro-fold
another viewer for pixel-10-pro-fold is running: http://127.0.0.1:8791/ (pid 4242, heartbeat 3 s ago)
```

### `phonelab/__main__.py`

- `serve` and `cua demo` both take `--device-tag` (default: derived).
- `--port` help text: `0 picks a free port and prints it`.
- After `resolve()`: `tag = args.device_tag or adb.tag`;
  `runs_root = Path(args.runs_dir)`; `device_dir = runs_root / tag`;
  `Registry(runs_root, tag)`; `serve(..., runs_dir=device_dir)`.
- `serve` returns 2 on `PortInUse`; `main` passes that through.

## Tests (all without a device)

- `tests/test_adb.py`: `device_tag` cases: `"Pixel 10 Pro Fold"`,
  `"Pixel_9"`, `"sdk gphone64 arm64"`, `""`, `"  --  "` → `unknown`;
  `resolve()` sets `adb.tag`.
- `tests/test_sessions.py`: tagged write lands under `<root>/<tag>/sessions/`;
  `write` stamps `device_tag`; legacy untagged file under `<root>/sessions/`
  is read; a file with another tag is dropped; `by_display` only sees own
  device; `Registry(root)` without a tag behaves as before (existing tests
  keep passing unchanged).
- `tests/test_presence.py` (new): start writes the JSON shape above;
  `others()` is `[]` when alone; a foreign live file is reported with
  `heartbeat_age_s`; a dead pid (injected `pid_alive`) and a stale
  heartbeat (injected clock) are pruned; `beat()` only rewrites after
  `HEARTBEAT_EVERY_S`; `stop()` removes the file and tolerates a missing one.
- `tests/test_server_port.py` (new), ephemeral ports only, never 8791:
  `bind_viewer(port=0)` returns a server whose real port is non-zero and
  `viewer_url` names it; a listening socket on `127.0.0.1:0` makes
  `bind_viewer` on that port raise `PortInUse` whose message contains the
  port number and `--port 0`; `serve()` with a fake adb on a busy port
  returns 2 and prints `error: port N …` to stderr without calling
  `adb.props()`. Use `SimpleNamespace`/fakes for adb, manager, registry.
  Always `server_close()` in `finally`.

## Out of scope

- Migrating old freeze directories; `phone-lab-verify` keeps its own
  `--runs-dir` flag (point it at the device dir).
- Any change to capture, compose, the UI, or `cua-driver` calls.
- Locking the registry across processes beyond the atomic rename we
  already do.

## Acceptance

- `python3 -m unittest discover -s tests -v` green, new tests included.
- `grep -rn` for the Fold's serial over the checkout is empty (the cockpit
  runs it before committing; the serial is never printed).
- README run section, `docs/feature-map.md` Known limits, `AGENTS.md`
  (one line), and `skills/phone-lab-verify/SKILL.md` registry path updated.
