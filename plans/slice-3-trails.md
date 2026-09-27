# Slice 3 — Trails: record and replay (spec)

Date: 2026-09-27. Stack per [ADR 001](../docs/adr-001-tech-stack.md): Python
3.12 plus Pillow, standard library only otherwise. Builds on slice 1
(`Adb`, `displays.inventory`, `Registry`, `CuaDriver`, `fixture_state`),
slice 2 (`TreeDumper`, `refs.assign_refs/find/tap_point`) and writes the
run directory that slice 4 reads: [docs/trace-format.md](../docs/trace-format.md).
Nothing in slices 1, 2 or 4 changes shape.

A **trail** is a short list of readable steps (launch, tap a ref, set text,
key, swipe, sleep, wait-for) with the display each ran on and a predicate
that must hold afterwards. **Record** executes a plain script live on the
device, resolves refs, reads the fixture oracle to fill the predicates, and
writes the trail plus a `record` run. **Replay** executes a trail again
without an LLM: every step re-captures every display before and after,
resolves the ref from a fresh tree, checks its predicate, and writes a
`replay` run. Both runs are exactly the trace-format layout, so
`python3 -m phonelab trace --runs-dir runs/phone-lab-runs/<device-tag>`
shows them and diffs two replays.

## Package layout (additions)

```
phonelab/
  trails.py     trail model: schemas, load/save/validate, script parsing, sha256 (no device)
  replay.py     Backend protocol, AdbBackend, RunWriter (trace format), Runner (record + replay)
  __main__.py   + `trail record` / `trail replay` subcommands
tests/
  fixtures/trail_fixture_five.json     one trail in the shape below (the cockpit replaces it with the recorded one)
  test_trails.py                      model, validation, script parsing
  test_replay.py                      RunWriter output validated against trace loaders; Runner on a FakeBackend
```

Gemini never runs `adb`, `app_process` or `cua-driver`; every test runs on
fakes. Only the cockpit touches the device.

## Hard rules

- No serial anywhere: not in the trail, not in any run file, not in a log
  line. `device` blocks are `{"model", "android_release", "api_level"}`.
  Every log line goes through `Adb.redact`.
- Actions run only on displays whose role is `agent` and only on the two Cua
  packages (`ai.cua.fixture.notes`, `ai.cua.android.demo`). A trail whose
  `session.allow_apps` names any other package is refused at load time
  (`TrailError`). The human's display is captured (per trace format) but
  never tapped, typed into, or sent keys.
- Every JSON file is written atomically (`.tmp` then `os.replace`), `indent=2`,
  trailing newline. `run.json` is written first with `result: null` and
  `steps: []`, rewritten after every step, and once more on exit.
- Refs are resolved at run time from a fresh tree. A trail never stores
  coordinates as the target; `x`/`y` appear only in `step.json`
  `action.detail` as what was resolved.
- The runner owns its Cua session: it creates it, renews the lease when
  under 30 s remain (checked before every step), writes the registry record
  (`owner: "phonelab trail"`, `last_action` per step) so the live viewer's
  strip is correct, and stops the session in a `finally`. Ctrl-C or SIGTERM
  → `result.status: "aborted"`, session stopped, files consistent.

## Trail file — `phone-lab.trail.v1`

```json
{
  "schema": "phone-lab.trail.v1",
  "name": "fixture-five",
  "created_at": "2026-09-27T15:02:11+02:00",
  "recorded_on": {"model": "Pixel 10 Pro Fold", "android_release": "17", "api_level": 37},
  "session": {"allow_apps": ["ai.cua.fixture.notes"], "label": "phone-lab trail fixture-five"},
  "steps": [
    {"name": "launch fixture", "display": "agent",
     "action": {"kind": "launch", "package": "ai.cua.fixture.notes", "fresh": true},
     "predicate": {"kind": "fixture_counter", "expected": 0, "timeout_ms": 8000}},
    {"name": "tap increment", "display": "agent",
     "action": {"kind": "tap", "ref": "e7f67h", "hint": {"class": "android.widget.Button", "label": "INCREMENT", "resource_id": "ai.cua.fixture.notes:id/increment"}},
     "predicate": {"kind": "fixture_counter", "expected": 1, "timeout_ms": 5000}},
    {"name": "wait for Count: 3", "display": "agent",
     "action": {"kind": "wait_for"},
     "predicate": {"kind": "text_present", "text": "Count: 3", "timeout_ms": 5000}}
  ]
}
```

- `name` matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$`.
- `display` is a role alias resolved at run time: `"agent"` → the logical
  id of the session's Cua display (`create` reply `display_id`). Only
  `"agent"` is accepted in v1; the literal is kept so v2 can add named
  displays without a schema break.
- `action.kind` and its own keys (mirrors trace-format `detail`):

  | `kind` | keys | replay does |
  |---|---|---|
  | `launch` | `package`, `fresh` (bool, default true) | `fresh` → `adb shell am force-stop <package>` first (Cua packages only); then `CuaDriver.launch(sid, package)`; records `target_id` on the runner |
  | `tap` | `ref`, `hint` (optional `{class,label,resource_id}` copied from the recorded node, informational for slice 5) | fresh tree → `find(tree, ref)` → `tap_point` → `snapshot` + `tap`, stale retry ≤ 3 |
  | `set_text` | `ref`, `text`, `clear_first` (default true) | fresh tree → node index → `TreeDumper.act(display, index, "focus")`; `clear_first` → `input -d <display> keycombination KEYCODE_CTRL_LEFT KEYCODE_A` then `input -d <display> keyevent KEYCODE_DEL`; then `input -d <display> text <escaped>` (spaces as `%s`, shell-quoted) |
  | `key` | `keycode` (string, e.g. `KEYCODE_BACK`) | `input -d <display> keyevent <keycode>` |
  | `swipe` | `from`, `to` (`[x, y]`), `duration_ms` (default 300) | `input -d <display> swipe x1 y1 x2 y2 duration` |
  | `wait_for` | none | nothing; the predicate is the step |
  | `sleep` | `ms` | `time.sleep` |

- `predicate` is `null` or one of:

  | `kind` | keys | holds when |
  |---|---|---|
  | `fixture_counter` | `expected` (int) | `fixture_state(adb)["counter"] == expected` (oracle read ≈ 1.3 s) |
  | `text_present` | `text` | some node of a fresh tree of the step's display has `text == text` or `desc == text` |
  | `ref_present` | `ref` | `find(tree, ref)` is not `None` after `assign_refs` |
  | `ref_absent` | `ref` | `find(tree, ref)` is `None` |

  Every predicate carries `timeout_ms` (default 5000). It is polled every
  `poll_ms` (default 400) until it holds or the timeout passes; the elapsed
  time is `result.detail.predicate_ms`.

## Record script

`trail record` reads a plain text script (file, or `-` for stdin), one step
per line, `#` comments and blank lines ignored:

```
launch ai.cua.fixture.notes
tap e7f67h
tap e7f67h
tap e7f67h
wait_for text_present "Count: 3"
```

Grammar (`shlex.split` per line; the first token is the kind):

```
launch <package> [--keep]                       fresh unless --keep
tap <ref> [expect <predicate>]
set_text <ref> <text> [--no-clear] [expect <predicate>]
key <KEYCODE> [expect <predicate>]
swipe <x1> <y1> <x2> <y2> [<duration_ms>] [expect <predicate>]
sleep <ms>
wait_for <predicate>
name <text>                                     names the NEXT step (optional)
<predicate> := fixture_counter <int> | text_present <text> | ref_present <ref> | ref_absent <ref>
```

An optional `--timeout-ms N` at the end of any line sets that step's
predicate timeout. When a line has no `expect`, the recorder **derives** the
predicate from what it observes right after the action:

- `launch` and `tap` on a fixture-oracle package (`ai.cua.fixture.notes`):
  poll the oracle until the counter differs from the value read before the
  action (for `launch` with `fresh`, until the oracle answers at all); record
  `fixture_counter expected=<observed>`. If the oracle never answers within
  the timeout the step is recorded with `predicate: null` and result
  `message: "no oracle; predicate omitted"`.
- `tap`/`set_text`/`key`/`swipe` elsewhere: `ref_present <ref>` when the
  action had a ref, else `null`.
- `set_text`: `text_present <text>`.
- `sleep`: `null`. `wait_for`: the line's predicate is required.

The recorder executes each line live exactly as replay would (same
`Runner.run_step`), then appends the step to the trail with the derived or
explicit predicate. A recording whose step fails (action refused, predicate
never observed) still writes the run directory and the partial trail and
exits 1; the trail is not written to the trails directory in that case, only
into the run directory as `trail.json`.

## `phonelab/trails.py`

```python
TRAIL_SCHEMA = "phone-lab.trail.v1"
ACTION_KINDS = ("launch", "tap", "set_text", "key", "swipe", "wait_for", "sleep")
PREDICATE_KINDS = ("fixture_counter", "text_present", "ref_present", "ref_absent")
ALLOWED_PACKAGES = ("ai.cua.fixture.notes", "ai.cua.android.demo")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
DEFAULT_TIMEOUT_MS = 5000

class TrailError(Exception): ...          # message safe to print

@dataclass
class Step:
    name: str
    display: str                          # "agent"
    action: dict                          # {"kind": ..., ...} validated
    predicate: dict | None                # {"kind": ..., "timeout_ms": N, ...} validated
    def to_json(self) -> dict

@dataclass
class Trail:
    name: str
    created_at: str
    recorded_on: dict | None
    session: dict                         # {"allow_apps": [...], "label": str}
    steps: list[Step]
    def to_json(self) -> dict             # schema first
    @staticmethod
    def from_json(data: dict) -> "Trail"  # validates everything; raises TrailError with the step index in the message

def validate_action(action: dict) -> dict          # fills defaults (fresh, clear_first, duration_ms); raises TrailError
def validate_predicate(pred: dict | None) -> dict | None   # fills timeout_ms; raises TrailError
def load_trail(path: Path) -> Trail
def save_trail(trail: Trail, path: Path) -> None   # atomic, indent=2, newline
def trail_sha256(path: Path) -> str                # of the file bytes
def default_label(name: str) -> str                # f"phone-lab trail {name}"
def parse_script_line(line: str) -> dict | None
    # None for blank/comment; else {"kind": ..., "action": {...}, "predicate": {...}|None, "name": str|None,
    #   "timeout_ms": int|None, "explicit_predicate": bool}; `name` lines return {"kind": "name", "name": text}
def parse_script(text: str) -> list[dict]          # applies `name` lines to the following step; raises TrailError(line number)
def derive_predicate(kind: str, action: dict, observed: dict) -> dict | None
    # the rule table above; `observed` = {"oracle_counter": int|None, "oracle_ok": bool}
```

Validation rules: unknown `kind` → `TrailError`; `tap`/`set_text` need a
non-empty `ref`; `launch` needs `package` in `ALLOWED_PACKAGES`; `swipe`
needs two `[x, y]` int pairs; `sleep` needs `ms > 0`; `fixture_counter`
needs an int `expected`; `text_present` a non-empty `text`; `ref_*` a
`ref`; `display` must be `"agent"`; `session.allow_apps` non-empty subset
of `ALLOWED_PACKAGES`.

## `phonelab/replay.py`

### Backend

Everything that touches the phone goes through one object so the Runner is
testable without a device:

```python
class Backend(Protocol):
    adb: Adb                                            # for redact and the device block
    driver: CuaDriver
    def inventory(self) -> list[Display]                # displays.inventory(adb)
    def screencap(self, sf_id: str) -> bytes | None     # adb.screencap
    def tree(self, logical_id: int) -> dict             # TreeDumper.tree with assign_refs applied; raises TreeError
    def act(self, logical_id: int, node_index: int, action: str) -> dict
    def shell(self, *args: str) -> str                  # adb.shell (used for input -d, am force-stop)
    def fixture_state(self) -> dict | None              # cua.fixture_state; None when the oracle does not answer
    def props(self) -> dict                             # adb.props()
    def start(self) -> None                             # dumper.start()
    def stop(self) -> None                              # dumper.stop()

class AdbBackend:  # the real one: __init__(adb, driver_path: Path, treedump_jar: Path)
```

### RunWriter — writes the trace format

```python
class RunWriter:
    def __init__(self, runs_dir: Path, run_id: str, kind: str, device: dict, trail_path_src: Path,
                 trail_name: str, step_count: int, source_run_id: str | None, displays: list[Display],
                 session: dict | None, command: str)
    run_dir: Path
    def begin(self) -> None                 # mkdir, copy trail.json, write run.json with result None and steps []
    def step_dir(self, index: int) -> Path  # mkdir steps/NNN
    def write_step(self, step_doc: dict) -> None      # atomic step.json; appends/replaces the steps summary in run.json
    def write_tree(self, index: int, phase: str, logical_id: int, tree_doc: dict) -> str   # returns the file name
    def write_png(self, index: int, phase: str, logical_id: int, png: bytes) -> str        # returns the file name
    def finish(self, status: str, message: str | None) -> dict   # final run.json; returns it
```

`run_id = f"{trail.name}-{kind}-{time.strftime('%Y%m%d-%H%M%S')}"`, made
unique with `-2`, `-3` … when the directory exists. All field names, types
and orderings follow `docs/trace-format.md` exactly; `tests/test_replay.py`
checks a written run with `phonelab.trace.list_runs/load_run/load_step` and
with the assertions `tests/test_trace.py` already makes on synthetic runs
(every `image` and tree file exists, `png_sha256` matches the bytes,
`result.status` mirrors the summary, `duration_ms` equals the phase sum
within 50 ms).

Tree conversion `to_tree_doc(reply: dict, logical_id: int, captured_at: float) -> dict`
(`phone-lab.tree.v1`): `read_ms = reply["cost_ms"]`, `package` = the first
window's package, `window_count = len(windows)`, and each node maps `desc →
content_desc`, `id → resource_id`, keeps `i, parent, depth, ref, class,
text, bounds, clickable, enabled, focused` (`ref` is `None` when the node
has none).

Captures `capture_all(backend, displays, step_dir, index, phase) -> tuple[list[dict], int]`:
re-run `backend.inventory()` first (a Cua snapshot changes the agent
display's SurfaceFlinger id), then for every non-ignored display in server
order: OFF → panel with `seq: null, image: null`; else `screencap(sf_id)`,
crop `status_bar_px` rows from human panels with Pillow, sha256, write
`<phase>-logical-<n>.png`. One failed capture → `image: null` plus
`capture_error`, never an exception. Panel fields exactly as the freeze
manifest plus `unique_id` and `image`; `seq` counts captures per display
within the run; `session` is the runner's registry record (plus
`lease_remaining_now_ms`) on the agent display, `null` elsewhere. Returns
the panel list and the phase's ms.

### Runner

```python
class Runner:
    def __init__(self, backend: Backend, registry: Registry, runs_dir: Path, *, capture_human: bool = True,
                 poll_ms: int = 400, log=print)
    def open_session(self, allow_apps: list[str], label: str) -> SessionRecord    # create, registry write, remember display_id
    def close_session(self) -> None                                              # stop, registry state "stopped"
    def resolve_display(self, alias: str) -> int                                 # "agent" → session display_id; TrailError otherwise
    def run_step(self, index: int, step: Step, writer: RunWriter, *, derive: bool = False) -> tuple[dict, dict | None]
        # returns (step_doc as written, predicate used) — the predicate is derived when `derive` and step.predicate is None
    def check_predicate(self, pred: dict, logical_id: int) -> tuple[bool, dict]  # one evaluation; detail for step.json
    def wait_predicate(self, pred: dict, logical_id: int) -> tuple[bool, int, dict]  # polls until timeout; (held, ms, detail)

def record(backend, registry, runs_dir: Path, trails_dir: Path, name: str, script_text: str, *,
           capture_human: bool = True) -> int       # exit code; writes runs_dir/<run_id>/ and trails_dir/<name>.json on success
def replay(backend, registry, runs_dir: Path, trail_path: Path, *, times: int = 1, source_run_id: str | None = None,
           capture_human: bool = True, stop_on_fail: bool = True) -> int   # exit code 0 only when every run passes
```

`run_step` order and phases (names as in the format):

1. Renew the lease if under 30 s remain (`CuaDriver.renew`), refresh the
   registry record.
2. `capture_before_ms`: `capture_all(... "before")`.
3. `tree_before_ms`: tree of the step's display (agent), written as
   `tree-before-logical-<n>.json`. For `tap`/`set_text` this tree is the one
   the ref is resolved from. A missing ref → `result.status: "fail"`,
   `message: "ref <r> not found in tree (<k> refs)"`, no action, but the
   after-captures still run.
4. `action_ms`: the verb, per the table. Stale-frame refusals (`frame_stale`,
   `stale_snapshot`) get a fresh snapshot and retry up to 3 times, counted
   in `result.detail.frame_stale_retries`. Other `CuaError` → `refused` or
   `error` status with the reason in `message`.
5. `wait_ms`: `wait_predicate` when there is one (in record mode with
   `derive`, the derivation loop described above). Detail carries
   `counter_before`, `counter_after`, `predicate_ms`, plus `observed_text`
   or `ref_found` for tree predicates.
6. `capture_after_ms`, `tree_after_ms`: as before.
7. Write `step.json` (`schema`, `index`, `name`, `action` with `display_id`
   resolved and `ref` or `null` and `detail` incl. `x`, `y`, `snapshot_id`
   for taps, `predicate` with `display_id` added, `result`, `timings`,
   `captures`, `trees`), update the registry `last_action`
   (`{"kind": action.kind, "at", "result": status, "detail": {"ref", "x", "y", "step": index}}`),
   log one line: `step 2/5 tap increment → ok 2590 ms (action 620, wait 640)`.

`replay` with `stop_on_fail` writes the remaining steps as `skipped`
(`timings.phases: {}`, no captures, `captures: {"before": [], "after": []}`,
`trees: {"before": {}, "after": {}}`) and `result.status: "fail"`.
`times > 1` runs the trail that many times, **one Cua session per run**
(after `am force-stop` a second `app launch` in the same session is refused
with `owned_task_missing`, measured on the Fold), each run with `launch
fresh: true` so counters start at 0, one run directory each, and prints a final line `replay fixture-five: 3/3
pass · 34.7 s, 33.9 s, 35.1 s`.

`record` derives predicates (`derive=True`), collects the steps into a
`Trail` (`recorded_on = backend.props()` plus model, `created_at` ISO with
offset), saves it as the run's `trail.json` and, when every step is `ok`,
to `trails_dir / f"{name}.json"`. The trail's `step_count` in `run.json`
is the number of script steps, known up front.

## `phonelab/__main__.py` (additions)

```
python3 -m phonelab trail record <script|-> --name NAME [--driver PATH] [--treedump-jar PATH]
        [--serial S | --model M] [--runs-dir DIR] [--device-tag TAG] [--trails-dir DIR] [--agent-only]
python3 -m phonelab trail replay <trail.json> [--times N] [--source-run-id ID] [--continue-on-fail]
        [--driver PATH] [--treedump-jar PATH] [--serial S | --model M] [--runs-dir DIR] [--device-tag TAG] [--agent-only]
```

Runs go under `<runs-dir>/<device-tag>/` (the device directory, same as
`serve`), so the trace viewer is pointed at that directory. `--trails-dir`
defaults to `<runs-dir>/<device-tag>/trails/`. `--agent-only` sets
`capture_human=False` (human panels still get manifest entries with
`image: null` and `capture_error: "skipped"`). `--driver` and
`--treedump-jar` resolve like `serve` (`PHONELAB_CUA_DRIVER`,
`PHONELAB_TREEDUMP_JAR`, repo jar); both are required for `trail`. SIGTERM
raises `KeyboardInterrupt` as the other commands do. Exit codes: 0 pass,
1 a run failed or was aborted, 2 usage/device selection.

## Tests (no device)

- `tests/test_trails.py`: round-trip `Trail.to_json/from_json` on the
  fixture; validation errors name the step index; `parse_script` on the
  five-line proof script yields five steps with the right kinds, `name`
  lines attach, `expect` clauses parse, `--timeout-ms` applies, a bad kind
  raises with the line number; `derive_predicate` table; a trail with a
  non-Cua `allow_apps` package is refused.
- `tests/test_replay.py`: a `FakeBackend` (in-memory: two displays — human
  logical 0 with `status_bar_px 20` and agent logical 98 — returning tiny
  PNGs, a fixture tree copied from `tests/fixtures/tree_cua_fixture.json`
  whose counter node text follows an internal counter that a tap at the
  increment centre increments, an oracle that answers the same counter, and
  a fake `CuaDriver` object recording `create/launch/snapshot/tap/renew/stop`
  calls with one `frame_stale` refusal on the first tap). Tests:
  1. `replay` of the fixture trail passes: 5 step dirs, `run.json` `result.status
     == "pass"`, `steps` summary length 5, each `step.json` loads through
     `trace.load_step`, PNG and tree files exist and hash-match, agent panel
     `image` is `after-logical-98.png`, human panel cropped height is
     `height - 20`, `frame_stale_retries == 1` on step 1, `x, y == (540, 263)`,
     `lease` renewed when the fake reports 20 s left, session stopped, registry
     record `state == "stopped"` and `owner == "phonelab trail"`.
  2. A trail whose second tap names a ref absent from the tree → step 1
     `fail` with the ref in `message`, steps 2–4 `skipped`, run `fail`;
     `--continue-on-fail` variant runs them.
  3. `record` from the five-line script derives `fixture_counter 0,1,2,3`
     and keeps the explicit `text_present`, writes `trails/<name>.json` that
     `load_trail` accepts and that equals the fixture trail apart from
     `created_at`.
  4. `times=3` → three run directories, distinct ids, three `create` and
     three `stop` calls on the fake driver, three `launch` calls.
  5. `capture_human=False` → human panel `image: null`,
     `capture_error: "skipped"`, no PNG written for logical 0.
  6. `trace.list_runs` on the runs dir lists the replays newest first.

Run with `python3 -m unittest discover -s tests -v`; the 88 existing tests
keep passing.

## Acceptance (proof on the Fold, cockpit only)

1. Unit tests pass (existing 88 plus the new ones).
2. `trail record` of the five-line script (`launch`, three `tap e7f67h`,
   `wait_for text_present "Count: 3"`) on the Cua display writes
   `runs/phone-lab-runs/pixel-10-pro-fold/fixture-five-record-<ts>/` with
   five `ok` steps, predicates `fixture_counter 0, 1, 2, 3` derived from the
   oracle and the explicit `text_present`, and
   `.../trails/fixture-five.json`.
3. `trail replay --times 3` of that trail is all green: three run
   directories, `result.status: "pass"` in each, 15/15 steps `ok`, refs
   resolved from fresh trees (never from stored coordinates), stale retries
   counted. Per-step and per-run timings reported (targets: tap step ≤ 6 s
   with both displays captured, run ≤ 60 s).
4. `python3 -m phonelab trace --port 0 --runs-dir runs/phone-lab-runs/pixel-10-pro-fold`
   lists the four runs, opens a replay step with both displays' before/after
   images and the tree, and diffs two replays (cockpit observation in the
   built-in browser, page text saved under the run directory).
5. One replay against a wrong ref fails loudly at that step with `fail`, the
   rest `skipped`, run `fail` (negative proof).
6. The serial grep over the checkout is clean; no Cua session, no treedump
   process, no `/data/local/tmp/phonelab/` left on the phone; the fixture is
   force-stopped.
