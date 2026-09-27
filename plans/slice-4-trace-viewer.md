# Slice 4 — Trace viewer (spec)

Date: 2026-09-27. Stack per [ADR 001](../docs/adr-001-tech-stack.md):
Python 3.12, standard library plus Pillow, one vanilla HTML page. Built ahead
of slice 3 because it needs no device; the run-directory schema it reads is
[docs/trace-format.md](../docs/trace-format.md), which slice 3 must write.
Until slice 3 lands, runs come from the synthetic generator in `tests/`.

Run tests with `python3 -m unittest discover -s tests -v`.

## Package layout (additions)

```
phonelab/
  trace.py          run index, run/step loaders, safe file lookup, trace HTTP routes, trace-only server
  ui/trace.html     the trace viewer (dark theme, no external resources)
  server.py         ViewerHandler.do_GET hands /trace, /api/runs*, /runs/* to trace.handle_get
  __main__.py       new subcommand: python3 -m phonelab trace [--host] [--port 8792] [--runs-dir]
tests/
  synth_run.py      synthetic run generator (small PIL images, no device); also a CLI
  test_trace.py     index, loaders, path safety, handler routes, generator round-trip
```

## Hard rules

- Nothing in this slice runs `adb`, `cua-driver`, or touches a device. The
  `trace` subcommand must not construct `Adb`.
- Default port for `trace` is **8792**; 8791 stays the live viewer's.
- No serial anywhere; the run's `device` block is model, release, API level.
- Files are served only from inside a validated run directory, `.png` and
  `.json` only (see format doc, "Reading rules").

## `phonelab/trace.py`

```python
RUN_SCHEMA = "phone-lab.run.v1"; STEP_SCHEMA = "phone-lab.step.v1"; TREE_SCHEMA = "phone-lab.tree.v1"
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
TRACE_UI_PATH = Path(__file__).resolve().parent / "ui" / "trace.html"

class TraceError(Exception):   # .status (404 | 400) and a message safe to return as JSON
def valid_run_id(run_id: str) -> bool                       # RUN_ID match and not "." / ".."
def run_dir(runs_dir: Path, run_id: str) -> Path            # raises TraceError(400) on a bad id, TraceError(404) when run.json is missing
def list_runs(runs_dir: Path) -> list[dict]                 # index entries, newest first (see /api/runs); corrupt run.json skipped
def load_run(runs_dir: Path, run_id: str) -> dict           # parsed run.json plus "step_dirs": [names of steps/* that exist]
def load_step(runs_dir: Path, run_id: str, index: int) -> dict
    # parsed steps/NNN/step.json; if the dir exists but the file is missing/corrupt:
    # {"schema": STEP_SCHEMA, "index": n, "pending": true}; TraceError(404) when the dir is absent
def safe_file(runs_dir: Path, run_id: str, rel: str) -> Path
    # rel must be relative, segments must match r"^[A-Za-z0-9._-]+$", no "..", suffix in {".png", ".json"};
    # resolved path must stay inside run_dir(); TraceError(400/404) otherwise
def handle_get(handler: BaseHTTPRequestHandler, runs_dir: Path, path: str) -> bool
    # routes below; returns False when the path is not a trace route so the caller can 404 its own way
def serve_trace(host: str = "127.0.0.1", port: int = 8792, runs_dir: Path = Path("runs/phone-lab-runs")) -> None
    # ThreadingHTTPServer with TraceHandler; GET / redirects (302) to /trace; prints one start line; Ctrl-C stops
```

`handle_get` uses the handler's `_send`/`_json` helpers when present (the
live `ViewerHandler` has them); the trace-only `TraceHandler` defines the
same two helpers with the same signatures so the routing code is shared.
Errors from `TraceError` become `{"error": msg}` with the error's status;
any other exception becomes a 500 `{"error": "..."}`, never a crashed
thread.

### Routes

| Route | Response |
|---|---|
| `GET /trace` | `ui/trace.html`, `text/html; charset=utf-8` |
| `GET /api/runs` | `[{"run_id", "kind", "created_at", "finished_at", "device", "trail": {"name", "step_count"}, "result", "timings", "step_count": len(steps)}]`, newest first by `timings.started_at`, then `created_at`, then name |
| `GET /api/runs/<run>` | `load_run` output |
| `GET /api/runs/<run>/steps/<n>` | `load_step` output; `<n>` is a non-negative int (leading zeros allowed) |
| `GET /runs/<run>/<rel-path>` | the file, `image/png` or `application/json`, `Cache-Control: no-store` |
| anything else | `False` (caller answers 404) |

`Cache-Control: no-store` on all responses (same as the live viewer).

## `phonelab/server.py` change

In `ViewerHandler.do_GET`, before the final `else`, add
`elif handle_get(self, self.server.runs_dir, path): pass`. Nothing else in
the live server changes; the live page keeps working with a device.

## `phonelab/__main__.py` change

Add `trace` subparser (`--host 127.0.0.1`, `--port 8792`, `--runs-dir
runs/phone-lab-runs`). `main()` must dispatch `trace` **before** resolving
`Adb`, so the command works with no device and no adb binary. The `cua`
driver check and the device resolution stay as they are for the other
commands.

## `phonelab/ui/trace.html`

Vanilla HTML/CSS/JS, dark theme with the same `:root` palette, fonts, and
header style as `index.html`; no external resources; all fetches relative
(`/api/runs`, `/runs/...`).

Layout:

- **Header**: "phone-lab · trace", a `<select id="run">` of runs (label
  `run_id · kind · result.status · N steps`), a second `<select id="diff">`
  with an empty first option "no diff" plus the same runs, step navigation
  (`◀` `▶` buttons, `<input type="number">` for the index, keyboard `←`/`→`
  and `j`/`k`), and a device line (`model · Android release`).
- **Timeline strip** under the header: one small box per step from
  `run.steps` (index and `kind`), coloured by status (ok green, healed blue,
  fail/refused/error red, skipped grey, pending muted); clicking selects the
  step; the current step is outlined. When a diff run is chosen, a second
  row of boxes for it is drawn under the first, aligned by index; boxes
  whose `status` differs between runs get a red border.
- **Step view** (`<main>`), for the selected step of the primary run:
  - Title: `step NNN · name · status badge · duration_ms ms`.
  - **Screens**: one column per display in `captures.before` order, each
    with the display name and `logical <id>` and two images stacked
    (`before`, `after`) loaded from `/runs/<run>/steps/NNN/<image>`;
    `image: null` renders the same "display off" placeholder as
    `index.html`; an `<img>` load error shows "missing file". Images use
    `max-height: 40vh`, click toggles a 90vh enlarged mode.
  - **Action** card: `kind`, `display_id`, `ref`, then `detail` as a
    two-column key/value list; unknown kinds render the same way.
  - **Predicate** card (hidden when `null`).
  - **Result** card: status badge, `message`, `detail` key/value list.
  - **Timings** card: `duration_ms`, then `phases` as a horizontal bar
    (one segment per phase, width proportional, label on hover and below).
  - **Tree** card: tabs `before`/`after`, then one tab per `logical-<n>`
    key; the tree file is fetched lazily on first view and drawn as an
    indented list (`depth` × 14 px), each line `class-short-name · text or
    content_desc · ref · bounds`; nodes with a `ref` get a monospace badge;
    the node whose `ref` equals `action.ref` is highlighted. A missing or
    corrupt tree file shows "no tree".
  - A `pending: true` step shows only "step not written yet".
- **Diff view**, when a diff run is selected: the step view becomes two
  columns, primary left and diff right, both at the same index (the right
  side shows "no such step" past the diff run's end). Under the step title
  a **diff summary** line lists, per display, `same screen` / `screen
  differs` from `png_sha256` equality of the `after` capture, `action
  same/differs` (deep-equal of `action`), `result same/differs`
  (`result.status`), `Δ duration +/-N ms`, and `tree: +A −R refs`
  (set difference of `ref` values over the `after` trees, computed once
  both files have loaded). Differing cards get a `.differs` red left
  border.
- State: the selected run, diff run, and step index are mirrored to the URL
  hash (`#run=<id>&diff=<id>&step=<n>`) so a link reproduces a view; on load
  the hash is honoured, otherwise the newest run and step 0.
- `/api/runs` is fetched once on load and on a "refresh" button; the step
  JSON is fetched on every navigation (no caching beyond the browser's).

## `tests/synth_run.py`

```python
def make_run(runs_dir: Path, run_id: str, *, steps: int = 5, kind: str = "replay", seed: int = 0,
             fail_at: int | None = None, drift_px: int = 0, size: tuple[int, int] = (108, 192),
             created_at: float = 1_790_000_000.0) -> Path
```

Writes a complete run per the format doc with no device: two displays
(`Inner Display` logical 0 role human 120x200 status bar 20 px, cropped;
`Cua agent` logical 98 role agent `size`), plus the OFF `Outer Display`
logical 3 as a `seq: null` panel. Each step draws a solid background with a
big counter digit (`ImageDraw.text`, default font) so before/after and
run-to-run differences are visible; the agent screen's counter increments
per step; `drift_px` shifts the increment button's bounds and centre in the
tree and `action.detail` so two runs differ. Step 0 is `launch`; steps 1…n
are `tap` with `predicate fixture_counter`; when `fail_at` is set that step
gets `status "fail"` with a message and later steps are `skipped` (no
captures, no phases), and `run.json` `result.status` is `"fail"`. Timings
are deterministic from `seed` and `created_at`. Trees are written for the
agent display only (before and after), with `ref` values derived from
`hashlib.sha1` of class+label+grid-centre, first 6 hex, so the same
element keeps its ref across runs and moves with drift. Returns the run
directory.

CLI: `python3 -m tests.synth_run --runs-dir DIR [--runs 2] [--steps 5]`
writes `synthetic-a-…` and `synthetic-b-…` (the second with `drift_px 12`
and `fail_at 3`) and prints their paths; used for the manual browser check
on port 8792.

## `tests/test_trace.py`

Using `tempfile.TemporaryDirectory()` and `make_run`, and driving the
handler through a real `TraceHandler` on `127.0.0.1` port 0 with
`http.client` (so routing, headers, and file serving are exercised
end-to-end; no fixed port):

1. `list_runs` skips `sessions/`, a `20260926/` freeze folder, an empty
   directory, and a run with a corrupt `run.json`; orders newest first.
2. `load_run` returns the schema and `step_dirs`; `load_step` returns the
   parsed step and `pending` for a step dir with no `step.json`.
3. `safe_file` rejects `../x.png`, absolute paths, `steps/000/x.txt`,
   `steps/000/%2e%2e`, a run id with a slash, and accepts
   `steps/000/before-logical-98.png` resolving inside the run.
4. HTTP: `/trace` is 200 HTML; `/api/runs` lists both synthetic runs;
   `/api/runs/<id>` and `/steps/2` return the schemas; `/steps/999` is 404;
   `/runs/<id>/steps/000/before-logical-98.png` is 200 `image/png` with a
   PNG signature; `/runs/<id>/../run.json` is 400 or 404; `/nope` is 404;
   `/` on the trace-only server redirects to `/trace`.
5. Generator round-trip: the failing run has `result.status == "fail"`,
   step `fail_at` is `fail`, later steps `skipped`; every `png_sha256` in
   every manifest equals the sha256 of the named file; every `image` and
   tree file named exists; `run.steps` length equals the number of step
   dirs.
6. `handle_get` mounted in the live `ViewerHandler` path: construct
   `ViewerServer` is out of scope (needs `Adb`); instead assert that
   `phonelab.server.ViewerHandler.do_GET` source references `handle_get`
   (a cheap guard that the hook is wired).

Existing tests must keep passing (22 as of slice 1 plus the device
selection tests from commit 7202e70).

## Acceptance (no device)

1. `python3 -m unittest discover -s tests -v` passes, including the new
   trace tests.
2. `python3 -m tests.synth_run --runs-dir /tmp/pl-runs` then
   `python3 -m phonelab trace --runs-dir /tmp/pl-runs --port 8792` serves
   `/trace`; in the browser the newest run opens, stepping shows the counter
   change between before and after, the failing run shows the red step and
   the skipped tail, and choosing it as the diff run shows `screen differs`,
   `Δ duration`, and the moved ref in the tree diff.
3. No `adb` call and no port 8791 during any of the above.

Device proof (open a real slice-3 run on the Fold) waits for slice 3 and is
recorded in the feature map as `implemented`, not `proven`.
