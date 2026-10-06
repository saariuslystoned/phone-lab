# Slice 6 — Numbered tap targets ("marks") (spec)

Date: 2026-10-06. Builds on slice 2 (`refs.py`, `tree.py`) and the
slice-1 display join. Stdlib plus Pillow; no new device code.

## Why

A human watching the viewer, or a vision agent looking at a screenshot,
says "tap 2" more easily than it picks a six-symbol ref out of tree JSON.
Marks put a number on every tap target of one display, draw the numbers
on a screenshot, and map a number back to a ref and a tap point.

## Module: `phonelab/marks.py` (no device)

- `select(tree, include_system=False) -> [{n, ref, label, class, bounds,
  tap, i, package, actionable}]`. Uses `refs.assign_refs`,
  `is_interesting`, `label_of`, `tap_point`. A node is marked when it has
  a ref, lives in an allowed window, covers < 90 % of its window, and is
  actionable (clickable, long-clickable, editable, checkable) or labelled.
  Allowed windows: the focused application window, else every application
  window; `include_system` adds status bar, navigation, IME, overlays. A
  label-only node inside a marked actionable ancestor folds into it (the
  ancestor borrows the label when it has none).
- `order(marks, tolerance=24)`: rows by top edge (a box joins a row when
  its top is within 24 px of the row's first top), left to right inside a
  row, ref as tie-breaker. Numbers depend on geometry only, not node order.
- `resolve(marks, token)`: `#N` → mark N, anything else → ref; `None` when
  absent, `ValueError` on `#x`.
- `render(png, marks, crop_status_bar_px)`: coloured box plus numbered
  badge per mark, then the status bar band cropped; badges never sit in
  the cropped band. Bounds are 1:1 with `screencap -d` pixels.

## CLI

- `phonelab marks <logical_id> [--out PATH] [--include-system] [--no-crop]`:
  one tree read, the logical → SurfaceFlinger join from `displays.inventory`,
  one `screencap -d`, overlay PNG (default
  `runs/phone-lab-runs/<device-tag>/marks/marks-<id>-<stamp>.png`) and the
  same JSON beside it; prints JSON with `marks`, `png`, `json`,
  `timings_ms` (tree, screencap, render, total). Refuses an OFF display.
- `phonelab tap <logical_id> '#N'|<ref> [--expect-ref REF] [--include-system]`:
  re-reads the tree, re-numbers, resolves, refuses (exit 3) when
  `--expect-ref` differs (stale overlay) or the target's package is not
  one of the two Cua apps plus `--app`, then `input -d <logical_id> tap x y`.
  Exit 1 on tree failure or an unknown target.
- Device selection as everywhere: `--serial`, `--model`, `ANDROID_SERIAL`,
  `--allow-emulators`.

## Privacy and safety

- Text outside the Cua apps is redacted by treedump as before, so labels
  of other apps are the id suffix or empty.
- `tap` on a human display is an explicit CLI action by the person at the
  keyboard; the package guard keeps it to Cua's synthetic apps unless
  `--app` names another. The viewer's `POST /api/tap` rule (agent displays
  only) is unchanged.
- Overlays are PNGs under git-ignored `runs/`; the status bar is cropped
  by default.

## Tests (`tests/test_marks.py`)

Filter (full-window skip, system windows out by default and in with the
flag, focused-window preference, no-window fallback, label folding),
ordering (node-order independence, row tolerance, numbering stable when
the counter changes), resolve, render (size, crop, badge below crop), CLI
parsing, `run_marks`/`run_tap` against a fake adb and dumper (overlay
written, `input -d` argv, stale and package refusals).

## Acceptance (Fold, inner panel, logical 0)

1. Full suite green.
2. `marks 0` with `ai.cua.fixture.notes` in front: overlay viewed, the
   INCREMENT number read from the image; two captures give identical
   number → ref lists for the stable elements.
3. Three `tap 0 '#N' --expect-ref <ref>` calls: Count rises by exactly 3
   (read from the tree). Timings recorded.
4. `tap 0 '#N' --expect-ref <wrong>` refuses with exit 3 and no tap.
5. Serial grep over the checkout and the run directory is clean.
