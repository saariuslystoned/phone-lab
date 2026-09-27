# Slice 2 — Cross-display element refs (spec)

Date: 2026-09-27. Stack per [ADR 001](../docs/adr-001-tech-stack.md):
Python 3.12 plus Pillow on the host, and one small Java program that runs
on the phone under the shell UID. Builds on the slice-1 server, registry,
and capture manager; nothing in slice 1 changes shape.

## Why a Java program on the phone

`uiautomator dump` sees nothing on Cua's virtual display, and Cua's own
accessibility capability is blocked in this runtime. A process started by
`app_process` from `adb shell` runs as the shell UID, may create a
`UiAutomation` bound to a `UiAutomationConnection`, and
`UiAutomation.getWindowsOnAllDisplays()` (public API since 30) returns the
window list of every display, keyed by logical display id. That is the
whole trick. The evidence campaign proved the tree read on the Cua display
this way through the legacy `uiautomator runtest` runner; this slice uses
the same UiAutomation but keeps the process alive and talks to it over
stdin/stdout so one tree costs a fraction of a second instead of a JVM
start per read. It connects with `FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`
so the human's own accessibility services keep running, falling back to
the plain connect and reporting which one it got.

Fallback if `app_process` cannot connect on the Fold: wrap the same class
in a `UiAutomatorTestCase` and run it through `uiautomator runtest` (one
process per read, ~1.5 s). The JSON shapes below do not change.

## Package layout (additions)

```
tools/treedump/
  build.sh                     javac + d8 + zip → tools/treedump/build/treedump.jar (git-ignored)
  src/lab/phone/treedump/Main.java
phonelab/
  tree.py          TreeDumper: push the jar, keep one device process, ask for trees
  refs.py          content-stable refs, ref lookup
  server.py        + GET /api/tree/<logical_id>, POST /api/tap, POST /api/tree/<logical_id>/act
  ui/index.html    + per-panel "refs" overlay, tap-by-ref click
  __main__.py      + `tree` command; `serve --driver --treedump-jar`
tests/
  fixtures/tree_cua_fixture.json      one tree reply in the shape below
  test_refs.py, test_tree.py, test_server_tree.py
```

`tools/treedump/build/` is git-ignored (add `tools/treedump/build/` to
`.gitignore`). The jar is built on the host by whoever runs the device;
Gemini never runs adb, `app_process`, or `cua-driver`.

## Privacy rules that the code enforces

- Display 0 is the human's screen. The device program records `text` and
  `desc` only for packages in its `--text-packages` allow-list; every other
  node gets `text: null`, `desc: null`, and `text_len` (character count,
  so a ref can still tell "has a label" from "no label"). The default
  allow-list is `ai.cua.fixture.notes,ai.cua.android.demo`. Nothing in this
  slice writes a display-0 tree to disk except the proof runner, under the
  git-ignored runs directory.
- Tap-by-ref and accessibility actions refuse any display whose role is not
  `agent`. The human's display is never tapped by phone-lab.
- Accessibility actions (`focus`, `click`) are performed only on nodes whose
  package is in `--act-packages` (default: the two Cua apps).
- The serial appears only in `adb -s` argument lists; every error and log
  line goes through `Adb.redact`.

## `tools/treedump/src/lab/phone/treedump/Main.java`

Package `lab.phone.treedump`, one file, no dependencies beyond `android.jar`.
Started as:

```
CLASSPATH=/data/local/tmp/phonelab/treedump.jar app_process /data/local/tmp/phonelab \
  lab.phone.treedump.Main [--text-packages a,b] [--act-packages a,b] [--max-nodes 2000] [--max-depth 40]
```

Startup: create a `HandlerThread`, obtain `UiAutomation` by reflection
(`android.app.UiAutomationConnection` no-arg constructor;
`UiAutomation(Looper, IUiAutomationConnection)` constructor; `connect(int)`
with `UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES`, and if that
throws, `connect()`), then `setServiceInfo` with flags
`FLAG_RETRIEVE_INTERACTIVE_WINDOWS | FLAG_REPORT_VIEW_IDS |
FLAG_INCLUDE_NOT_IMPORTANT_VIEWS`. Print one hello line and then serve
commands from stdin, one per line, one JSON object per reply line, until
`quit` or EOF. Every reply carries `"ok"` and, on failure, `"error"` (a
short message, never a stack trace longer than one line). All JSON is built
with `org.json.JSONObject`/`JSONArray` (in `android.jar`). Exit code 0 on
`quit`/EOF, 2 when UiAutomation could not be connected (hello line has
`ok: false`).

Hello line:

```json
{"ok": true, "hello": "phone-lab treedump 1", "pid": 12345, "connect_flags": 1,
 "suppresses_services": false, "text_packages": ["ai.cua.fixture.notes", "ai.cua.android.demo"]}
```

Commands and replies:

- `displays` → `{"ok": true, "display_ids": [0, 101]}` (the keys of
  `getWindowsOnAllDisplays()`).
- `tree <displayId>` →

```json
{"ok": true, "display_id": 101, "captured_at_ms": 1790000000000, "cost_ms": 87,
 "windows": [{"w": 0, "id": 42, "type": 1, "type_name": "application", "title": "Cua Synthetic Notes",
              "package": "ai.cua.fixture.notes", "layer": 0, "bounds": [0, 0, 1080, 1920],
              "focused": true, "active": true}],
 "nodes": [{"i": 0, "parent": null, "w": 0, "depth": 0, "class": "android.widget.FrameLayout",
            "package": "ai.cua.fixture.notes", "id": null, "text": null, "desc": null, "text_len": 0,
            "bounds": [0, 0, 1080, 1920], "clickable": false, "long_clickable": false, "editable": false,
            "checkable": false, "checked": false, "enabled": true, "focusable": false, "focused": false,
            "visible": true, "scrollable": false, "children": 1}]}
```

  `type_name` maps `AccessibilityWindowInfo` types: 1 application, 2
  input_method, 3 system, 4 accessibility_overlay, 5 split_screen_divider,
  6 magnification_overlay, else `unknown`. Nodes are depth-first in child
  order, `i` is the index in the array, `parent` the parent's index, `w`
  the window index. `id` is `getViewIdResourceName()`. Bounds are
  `getBoundsInScreen` as `[left, top, right, bottom]` in display pixels.
  Walk stops at `--max-depth` and `--max-nodes` and reports
  `"truncated": true` when it did. `getWindowsOnAllDisplays()` returning
  no list for the id → `{"ok": false, "error": "no windows on display 7",
  "display_id": 7, "windows": [], "nodes": []}`. Nodes are recycled after
  the walk (`AccessibilityNodeInfo.recycle()` where the API still has it;
  ignore if deprecated). The last `tree` reply's node list per display is
  kept (indices to `AccessibilityNodeInfo`) so `act` can address a node
  by index until the next `tree` for that display.
- `act <displayId> <nodeIndex> <focus|click>` → `performAction`
  (`ACTION_FOCUS` / `ACTION_CLICK`) on that node from the last tree of that
  display; refused (`ok: false, error: "package not allowed"`) when the
  node's package is not in `--act-packages`; `{"ok": true, "performed":
  bool}` otherwise.
- `toast <displayId> <text…>` → best-effort text toast on that display by
  reflection on the notification service (`ServiceManager.getService
  ("notification")`, `INotificationManager.Stub.asInterface`, a method named
  `enqueueTextToast`; fill parameters by type: `String` → `"com.android.shell"`,
  `IBinder` → a new `Binder`, `CharSequence` → text, first `int` → duration 1
  (long), `boolean` → false, second `int` → displayId, anything else → null).
  Reply `{"ok": true, "method": "<signature>"}` or `{"ok": false, "error": "..."}`.
  If this fails on the device, the proof uses `act … focus` on the fixture
  editor to raise the IME window instead; both are transient windows on the
  agent display and the proof says which one it used.
- `quit` → `{"ok": true, "bye": true}` and exit.

Unknown command → `{"ok": false, "error": "unknown command"}`. A malformed
argument → `{"ok": false, "error": "usage: tree <displayId>"}` and the
process keeps serving.

## `tools/treedump/build.sh`

POSIX sh. Locates the SDK from `ANDROID_SDK_ROOT`, `ANDROID_HOME`, or
`~/Library/Android/sdk`; picks the highest `platforms/android-*/android.jar`
and the highest `build-tools/*/d8`. Steps: `javac --release 17 -cp
android.jar -d build/classes src/lab/phone/treedump/*.java`; `d8 --release
--lib android.jar --output build/dex build/classes/lab/phone/treedump/*.class`;
`zip -j build/treedump.jar build/dex/classes.dex`. Prints the jar path and
its sha256; exits non-zero with a one-line reason when a tool is missing.

## `phonelab/tree.py`

```python
REMOTE_DIR = "/data/local/tmp/phonelab"
DEFAULT_TEXT_PACKAGES = ("ai.cua.fixture.notes", "ai.cua.android.demo")

class TreeError(Exception): ...          # message redacted

def parse_reply(line: str) -> dict         # json; raises TreeError on non-JSON or missing "ok"

class TreeDumper:
    def __init__(self, adb: Adb, jar: Path, text_packages=DEFAULT_TEXT_PACKAGES,
                 act_packages=DEFAULT_TEXT_PACKAGES, timeout: float = 10.0)
    def push(self) -> dict        # mkdir -p REMOTE_DIR; push the jar only when `sha256sum` on the device differs; {"pushed": bool, "sha256": str}
    def start(self) -> dict       # push(), spawn `adb -s S shell CLASSPATH=… app_process … Main --text-packages …`, read the hello line; returns it
    def stop(self) -> None        # send quit, wait 2 s, kill the subprocess; `pkill -f lab.phone.treedump` on the device as a last resort
    def displays(self) -> list[int]
    def tree(self, logical_id: int) -> dict       # one `tree` round-trip; restarts the process once on EOF/timeout, then raises TreeError
    def act(self, logical_id: int, node_index: int, action: str) -> dict
    def toast(self, logical_id: int, text: str) -> dict
    hello: dict | None; restarts: int; last_error: str | None
```

One lock serialises commands. Stdout lines that are not JSON (Android
runtime warnings) are skipped, at most 20 per reply, and logged through
`Adb.redact`. The subprocess is started with `subprocess.Popen(..., stdin=PIPE,
stdout=PIPE, stderr=PIPE, text=True, bufsize=1)`; the text must be sent with
a trailing newline; `adb shell` (not `exec-out`) is used so stdin is
forwarded. A `tree` for a display id that the device does not have returns
the `ok: false` reply as a dict (the server turns that into a 404).

## `phonelab/refs.py`

```python
GRID = 10
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # 31 symbols, no 0/o/1/l/i
REF_LEN = 6

def label_of(node: dict) -> str    # text, else desc, else the id after "/" ("" when none); when text is redacted, "" (text_len is not a label)
def centre(bounds: list[int]) -> tuple[int, int]
def grid(value: int, step: int = GRID) -> int      # round to nearest multiple of `step`, half up
def ref_key(node: dict) -> str     # f"{class}|{label}|{grid(cx)}|{grid(cy)}"
def make_ref(key: str) -> str      # sha256(key) → integer → base-31 digits from ALPHABET, first REF_LEN symbols
def is_interesting(node: dict) -> bool   # visible, non-empty bounds, and (clickable or long_clickable or editable or checkable or focusable or label_of(node) or id)
def assign_refs(tree: dict) -> dict
    # adds "ref" (str) to every interesting node in place; collisions within one tree get "-2", "-3" … in node order;
    # returns {"count": N, "refs": {ref: node_index}} and stores it as tree["refs"]
def find(tree: dict, ref: str) -> dict | None
def tap_point(node: dict) -> tuple[int, int]       # centre, clamped inside bounds
```

Two captures of the same screen give the same ref for the increment button
because class, label, and the 10 px-gridded centre are equal; a toast, an
IME window, or a counter text change elsewhere does not touch that key.
The grid makes a 4 px layout jitter invisible; a move of more than 5 px in
one axis may change the ref, and that is what slice 5 will heal.

## `phonelab/server.py` (additions)

`ViewerServer` gains `dumper: TreeDumper | None`, `driver: CuaDriver | None`,
and `trees: dict[int, dict]` (last tree per logical id, for `act` by ref).

- `GET /api/tree/<logical_id>` → the `tree` reply with refs assigned, plus
  `"display"`: the matching `/api/state` display entry (`logical_id`,
  `unique_id`, `sf_id`, `name`, `role`, `width`, `height`) or `null` when
  the id is unknown to the capture manager. Status 503 with `{"error":…}`
  when no dumper is configured or it raised `TreeError`; 404 when the
  reply was `ok: false` because the display has no windows. Response
  header `X-Tree-Cost-Ms`.
- `POST /api/tap` body `{"logical_id": 101, "ref": "k7m2pq"}` → refuses
  (`400`, `{"ok": false, "reason": "..."}`) when: the display is not role
  `agent` (`"not an agent display"`), there is no active registry session
  for it with a `target_id` (`"no phone-lab session on display 101"`), no
  driver is configured (`"no cua-driver"`), or the ref is not in a fresh
  tree (`"ref not found"`). Otherwise reads a fresh tree, resolves the
  point with `tap_point`, calls `driver.snapshot(sid, target)` then
  `driver.tap(sid, snapshot_id, x, y)`, retrying `frame_stale` /
  `stale_snapshot` up to three times with a fresh snapshot, and returns
  `{"ok": true, "logical_id": 101, "ref": "k7m2pq", "x": 540, "y": 263,
  "session_id": "…", "snapshot_id": "…", "frame_age_ms": 130,
  "stale_retries": 0, "tree_cost_ms": 90, "cost_ms": 1450}`. A `CuaError`
  becomes `502 {"ok": false, "reason": "refused:<reason>"|"error:<reason>"}`.
  The registry record for that session gets `last_action = {"kind": "tap
  ref", "at", "result", "detail": {"ref", "x", "y", "frame_age_ms",
  "stale_retries"}}` so the viewer strip shows it (the record is loaded,
  updated, and written; owner unchanged).
- `POST /api/tree/<logical_id>/act` body `{"action": "toast", "text": "…"}`
  or `{"action": "focus"|"click", "ref": "…"}` → agent displays only
  (`400` otherwise); `toast` forwards to `TreeDumper.toast`; `focus`/`click`
  resolve the ref against the server's last tree for that display (the node
  index is what the device process needs) and forward to `TreeDumper.act`.
  Returns the dumper reply with `"logical_id"` added.

`serve(...)` gains `driver: Path | None` and `treedump_jar: Path | None`;
when the jar is given the dumper is started before the HTTP server and
stopped after it (also on Ctrl-C), and the hello line is printed once,
redacted. `GET /api/state` gains `"tree": {"available": bool, "hello":
{…}|null, "restarts": n, "last_error": str|null}`.

## `phonelab/ui/index.html` (additions)

- Each panel header gets a `refs` toggle button and a small `all` checkbox
  (hidden until refs are on). When refs are on, the panel fetches
  `/api/tree/<logical_id>` every 1000 ms (only while on; stop when off or
  when the panel disappears) and draws an overlay `<div>` positioned over
  the `<img>`: one box per node with a ref (clickable/editable/checkable
  nodes only, unless `all`), scaled by `img.clientWidth / display.width`
  and `img.clientHeight / display.height` (the live frame is the whole
  display, not cropped), with a tiny chip showing the ref at the box's
  top-left. Boxes never intercept clicks on human panels; on agent panels
  a click on a chip POSTs `/api/tap` with that ref and shows the result in
  the header toast (`tap k7m2pq → ok (540,263) 1450 ms` or the reason).
- Under the footer, a `tree` line: `tree: 23 nodes · 7 refs · 90 ms · age
  0.4 s`, red when the last fetch failed with the error text.
- Trees for OFF displays are not fetched.

## `phonelab/__main__.py` (additions)

```
python3 -m phonelab tree <logical_id> [--treedump-jar PATH] [--serial S | --model M]   # one-shot: start, tree, print JSON with refs, stop
python3 -m phonelab serve … [--driver PATH] [--treedump-jar PATH]
```

`--treedump-jar` defaults to `PHONELAB_TREEDUMP_JAR`, then
`tools/treedump/build/treedump.jar` relative to the repo when it exists;
`--driver` defaults to `PHONELAB_CUA_DRIVER`. Without a jar, `serve`
runs exactly as slice 1 and `/api/tree/*` answers 503.

## Tests (no device)

- `tests/fixtures/tree_cua_fixture.json`: a tree reply in the shape above
  for the fixture screen (window "Cua Synthetic Notes", nodes for the
  content frame, title TextView "Synthetic Notes Fixture", EditText
  `ai.cua.fixture.notes:id/editor` bounds `[24,117,1056,215]`, Button
  `ai.cua.fixture.notes:id/increment` text "INCREMENT" bounds
  `[24,215,1056,311]`, TextView `…:id/counter` "Count: 0" bounds
  `[24,311,1056,380]`). The cockpit replaces it with a captured one once the
  device run exists; keep the same file name.
- `tests/test_refs.py`: the increment button's ref is a 6-symbol string over
  `ALPHABET`; identical for two copies of the fixture tree; unchanged when
  the counter text changes, when an extra `system` window with a toast
  node is appended, and when the button's bounds shift by ≤ 4 px in both
  axes; different when the label changes; collisions get `-2`; `find`
  returns the node; `tap_point` is the centre.
- `tests/test_tree.py`: `parse_reply` on good, non-JSON, and `ok: false`
  lines; `TreeDumper.tree` against a fake `adb` script (a Python script on
  PATH standing in for `adb shell` that speaks the protocol) covering one
  good round-trip, skipped noise lines, and one restart after EOF.
- `tests/test_server_tree.py`: `ViewerHandler` on an ephemeral port with a
  fake dumper (returns the fixture tree) and a fake driver (records
  `snapshot`/`tap` calls): `/api/tree/101` returns refs and the display
  entry; `/api/tree/0` tap is refused as a human display; `/api/tap` with
  no session is refused; with a registry record it calls snapshot then tap
  at `(540, 263)` and writes `last_action.kind == "tap ref"`; `/api/tree/7`
  → 404 when the fake replies `ok: false`; 503 without a dumper.

Run with `python3 -m unittest discover -s tests -v`; slice-1 tests keep
passing.

## Acceptance (proof on the Fold, cockpit only)

1. Unit tests pass; `tools/treedump/build.sh` produces the jar.
2. `python3 -m phonelab tree 0 --model "Pixel 10 Pro Fold"` returns a tree
   for the inner panel with redacted text (`text: null`, `text_len > 0` on
   at least one node) and `python3 -m phonelab tree <cua id>` returns the
   fixture's nodes with text, the hello line reporting whether services
   were suppressed.
3. Ten `GET /api/tree/<cua id>` captures over ≥ 20 s give the same ref for
   the increment button (`id` ends with `/increment`); one capture taken
   while a toast (or the IME, if the toast route fails) is visible on that
   display gives the same ref and lists the extra window; the counter text
   changes between captures and the ref does not.
4. Tap-by-ref: with `cua demo --no-taps` holding the session and a
   synthetic human (`ai.cua.android.demo` on display 0 with its editor
   focused, typed into with `input -d 0 text`), ten `POST /api/tap` calls
   with the increment ref all return `ok`; the fixture oracle counter rises
   by exactly ten; the demo oracle's editor text equals exactly what was
   typed on display 0; the demo keeps `window_focus`; the demo's event log
   shows no touch from the taps. Tree cost and tap cost are measured and
   reported (targets: tree ≤ 500 ms, tap ≤ 2 s).
5. The viewer shows the overlay on the Cua panel with the increment ref
   chip, and clicking it increments the counter (cockpit observation in the
   built-in browser, page text saved under the run directory).
6. The serial grep over the checkout is clean; no Cua session and no
   `treedump` process is left on the phone; `/data/local/tmp/phonelab/`
   holds only the jar (remove it at the end of the run).
