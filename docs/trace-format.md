# Trace format — the run directory that trails write and the trace viewer reads

Status: defined 2026-09-27 for slice 4 (trace viewer), ahead of slice 3
(trails: record and replay). Slice 3 must write exactly this layout; slice 4
reads it and nothing else. Any field added later must be additive and the
`schema` strings must be bumped (`v1` → `v2`) when a reader could break.

Conventions follow `phonelab/server.py` (freeze manifest
`phone-lab.freeze.v1`), `phonelab/sessions.py` (`SessionRecord.to_json()`)
and `phonelab/displays.py` (`to_json(Display)`): JSON with `indent=2` and a
trailing newline, written atomically (`.tmp` then `os.replace`), Unix epoch
floats for machine times (`*_at`), ISO 8601 with local offset for the
human-readable `created_at`/`finished_at`, milliseconds as ints (`*_ms`),
SHA-256 as 64 lowercase hex characters. Everything under `runs/` is
git-ignored; PNGs never leave the machine. **No field ever holds a device
serial**; the device block is `{"model", "android_release", "api_level"}`
exactly as `/api/state` reports it.

## Layout

```
runs/phone-lab-runs/
  <run_id>/                       one recording or one replay
    run.json                      phone-lab.run.v1 — device, trail, displays, step summary, result
    trail.json                    copy of the trail that was recorded or replayed (slice 3 shape)
    steps/
      000/                        one directory per step, zero-padded to three digits, from 000
        step.json                 phone-lab.step.v1 — action, result, timings, captures, tree file names
        before-logical-0.png      full-resolution screencap per display before the action
        before-logical-98.png
        after-logical-0.png       … and after the action (and after the predicate settled)
        after-logical-98.png
        tree-before-logical-98.json   phone-lab.tree.v1 per display where a tree was read
        tree-after-logical-98.json
      001/
      …
    STATE.md, events.jsonl, heartbeat, PROOF.md    optional long-run bookkeeping (not read by the viewer)
```

- `<run_id>` matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$` and is not `.` or
  `..`. Recommended: `<trail-name>-<record|replay>-<YYYYMMDD-HHMMSS>`, e.g.
  `fixture-five-replay-20260928-021530`. A directory under the runs dir is a
  run **only if** it contains a readable `run.json` with
  `"schema": "phone-lab.run.v1"`; `sessions/`, `<YYYYMMDD>/` freeze folders
  and `overnight-*` folders are not runs and the viewer skips them.
- Per-display file names use the **logical display id** (`logical-<n>`),
  which is stable for the life of a Cua session and matches the
  `logical-<n>` lookup the live viewer already accepts. SurfaceFlinger ids
  change on every Cua snapshot and are recorded in the manifest, never in a
  file name. `uniqueId` contains commas and spaces and is likewise kept in
  the manifest only.
- Ignored displays (role `ignored`) are never captured. OFF displays get a
  manifest entry with `seq: null` and no PNG, exactly like a freeze panel.
- Every path inside the JSON files is relative to the directory of the JSON
  file that names it, uses `/`, and never starts with `/` or contains `..`.

## `run.json` — `phone-lab.run.v1`

```json
{
  "schema": "phone-lab.run.v1",
  "run_id": "fixture-five-replay-20260928-021530",
  "kind": "replay",
  "created_at": "2026-09-28T02:15:30+02:00",
  "finished_at": "2026-09-28T02:16:04+02:00",
  "device": {"model": "Pixel 10 Pro Fold", "android_release": "17", "api_level": 37},
  "trail": {"name": "fixture-five", "path": "trail.json", "sha256": "<64 hex>", "step_count": 5,
            "source_run_id": "fixture-five-record-20260928-020000"},
  "displays": [ { …to_json(Display)… , "unique_id": "…", "logical_id": 98, "role": "agent" } ],
  "session": { …SessionRecord.to_json()… } ,
  "steps": [
    {"index": 0, "dir": "steps/000", "name": "launch fixture", "kind": "launch",
     "status": "ok", "duration_ms": 1410, "display_id": 98}
  ],
  "result": {"status": "pass", "steps_total": 5, "steps_ok": 5, "steps_failed": 0, "message": null},
  "timings": {"started_at": 1790000130.2, "finished_at": 1790000164.9, "total_ms": 34700},
  "tool": {"name": "phonelab", "version": "0.1", "command": "replay"}
}
```

| Field | Meaning |
|---|---|
| `kind` | `"record"` or `"replay"`. |
| `device` | Same three keys as `/api/state` `device`. Never a serial. |
| `trail.path` | Relative file, normally `trail.json`. `sha256` is of that file's bytes. `source_run_id` is the recording a replay came from, or `null` for a recording. |
| `displays` | The inventory at run start, `to_json(Display)` per display, all roles included, server order (human by logical id, agents, then ignored). |
| `session` | The Cua `SessionRecord.to_json()` used for the run, or `null` when the trail drove no Cua session. |
| `steps` | Ordered summary, one entry per `steps/NNN/step.json`, so the viewer can draw the timeline without opening every step. `status` mirrors `step.json` `result.status`. |
| `result.status` | `"pass"` (every step ok or healed), `"fail"` (a step failed and the run stopped or continued), `"aborted"` (Ctrl-C, lost lease, device gone), or `null` while the run is still writing. |
| `timings` | Epoch floats plus the total in ms. |

Slice 3 writes `run.json` first with `result: null` and an empty `steps`
list, rewrites it after every step, and a final time on exit, so a viewer
opened mid-run shows a partial run and never a missing file.

## `steps/NNN/step.json` — `phone-lab.step.v1`

```json
{
  "schema": "phone-lab.step.v1",
  "index": 2,
  "name": "tap increment",
  "action": {
    "kind": "tap",
    "display_id": 98,
    "ref": "b7f3a1",
    "detail": {"x": 540, "y": 263, "snapshot_id": "snap-…", "package": "ai.cua.fixture.notes"}
  },
  "predicate": {"kind": "text_present", "display_id": 98, "text": "Count: 3", "timeout_ms": 5000},
  "result": {
    "status": "ok",
    "message": null,
    "detail": {"counter_before": 2, "counter_after": 3, "frame_stale_retries": 0, "predicate_ms": 640}
  },
  "timings": {
    "started_at": 1790000141.02, "finished_at": 1790000143.61, "duration_ms": 2590,
    "phases": {"capture_before_ms": 610, "tree_before_ms": 180, "action_ms": 620,
               "wait_ms": 640, "capture_after_ms": 520, "tree_after_ms": 20}
  },
  "captures": {
    "before": [
      {"sf_id": "11529215047354549223", "unique_id": "virtual:com.android.shell,2000,Cua agent,89",
       "logical_id": 98, "name": "Cua agent", "role": "agent", "seq": 41,
       "captured_at": 1790000141.05, "capture_ms": 510, "width": 1080, "height": 1920,
       "png_sha256": "<64 hex>", "cropped_status_bar_px": 0, "image": "before-logical-98.png",
       "session": { …SessionRecord.to_json() plus "lease_remaining_now_ms"… }},
      {"sf_id": "4619827677550801153", "unique_id": "local:4619827677550801153",
       "logical_id": 3, "name": "Outer Display", "role": "human", "seq": null,
       "captured_at": null, "capture_ms": null, "width": 1080, "height": 2364,
       "png_sha256": null, "cropped_status_bar_px": 0, "image": null, "session": null}
    ],
    "after": [ …same shape… ]
  },
  "trees": {
    "before": {"logical-98": "tree-before-logical-98.json"},
    "after":  {"logical-98": "tree-after-logical-98.json"}
  }
}
```

### `action`

`kind` is one of the slice-3 verbs; `display_id` is the logical display the
action ran on; `ref` is the slice-2 element ref when the action targets an
element, else `null`; `detail` holds the verb's own arguments.

| `kind` | `detail` keys |
|---|---|
| `launch` | `package`, optional `activity`, optional `extras`, optional `via` (`"cua"` or `"am"`) |
| `tap` | `x`, `y` (resolved from ref or label at run time), `snapshot_id` (when via Cua), optional `via` (`"cua"` or `"input"`), optional `label` |
| `set_text` | `text`, `clear_first`, optional `label` |
| `key` | `keycode` |
| `swipe` | `from`, `to` (`[x, y]`), `duration_ms` |
| `resize` | `size` (`"reset"` or `"<width>x<height>"`), optional `density` |
| `wait_for` | none (the predicate is the action) |
| `sleep` | `ms` |

New verbs add rows here; the viewer shows unknown kinds as `kind` plus the
pretty-printed `detail`, so it never breaks on one.

### `predicate`

`null` for steps without a check. Otherwise `kind` in `text_present`,
`ref_present`, `ref_absent`, `fixture_counter` (the fixture's content
provider), with `display_id`, `timeout_ms`, and the kind's own arguments.

### `result`

`status` is one of:

| `status` | Meaning |
|---|---|
| `ok` | Action delivered and the predicate (if any) held within its timeout. |
| `fail` | The predicate did not hold, or the action was delivered but the check failed. |
| `refused` | Cua refused the action (`message` carries the reason, e.g. `frame_stale` after the retry budget). |
| `error` | Exception, adb failure, lost lease. |
| `healed` | Slice 5: ref was missing, a candidate inside the bound was used; `detail.heal` describes it (HealResult.to_json(): status, ref, note). |
| `skipped` | Not attempted because an earlier step failed and the trail stops on failure. |

`message` is a short human line or `null`; `detail` is free-form but must be
JSON-serialisable and must not contain image bytes.

### `timings`

`started_at`/`finished_at` bound the whole step including captures;
`duration_ms = round((finished_at - started_at) * 1000)`. `phases` is a flat
map of named ms ints; the six names above are the standard ones, and any
phase may be absent when it did not run (a `skipped` step has none).

### `captures`

`before` and `after` are lists of **freeze panels**: the same fields the
freeze manifest writes for a panel (`sf_id`, `logical_id`, `name`, `role`,
`seq`, `captured_at`, `capture_ms`, `width`, `height`, `png_sha256`,
`cropped_status_bar_px`, `session`), plus `unique_id` and `image`, the PNG
file name relative to the step directory, `null` when no frame exists
(display OFF, or capture failed; then `capture_error` may carry the reason).
`width`/`height` are the stored PNG's size, after any crop.
`cropped_status_bar_px` follows the freeze rule: human panels lose the
status bar before the PNG is written; agent panels are stored whole.
Panels are listed in display order and both lists cover the same displays.

### `trees`

Map of `logical-<n>` → file name for every display where a tree was read,
separately for before and after. Missing key means no tree was read for
that display in that phase. `trees.heal` = `{"recorded": ..., "current": ...}`
is present whenever heal ran, and `run.json` has `heal.max_distance_px`.

## `tree-<phase>-logical-<n>.json` — `phone-lab.tree.v1`

The slice-2 accessibility read, flattened so a viewer can draw it without
recursion and a diff can key on refs.

```json
{
  "schema": "phone-lab.tree.v1",
  "display_id": 98,
  "captured_at": 1790000141.3,
  "read_ms": 180,
  "package": "ai.cua.fixture.notes",
  "window_count": 1,
  "nodes": [
    {"i": 0, "parent": null, "depth": 0, "ref": null, "class": "android.widget.FrameLayout",
     "text": null, "content_desc": null, "resource_id": null,
     "bounds": [0, 0, 1080, 1920], "clickable": false, "enabled": true, "focused": false},
    {"i": 7, "parent": 3, "depth": 3, "ref": "b7f3a1", "class": "android.widget.Button",
     "text": "+", "content_desc": "Increment", "resource_id": "ai.cua.fixture.notes:id/increment",
     "bounds": [420, 210, 660, 316], "clickable": true, "enabled": true, "focused": false}
  ]
}
```

`nodes` is in pre-order; `i` is the node's index in the list, `parent` the
index of its parent (or `null` for a root); `ref` is the slice-2 short ref
(present on nodes that have a label or are actionable, `null` on structural
nodes); `bounds` is `[left, top, right, bottom]` in display pixels. Slice 2
may add fields; it must keep these.

## Reading rules for the viewer

- Enumerate runs as the immediate children of the runs dir that contain a
  parsable `run.json` with the right schema; sort by `timings.started_at`
  descending, falling back to `created_at`, then name.
- A corrupt or half-written `step.json` (slice 3 writes it atomically, but
  the step directory can exist before it) renders as "step not written yet",
  never a viewer error.
- Serve files only from inside the selected run directory and only with the
  extensions `.png` and `.json`; reject any run id or path segment that fails
  the pattern above or contains `..`.
- Diffs are computed client-side from two `step.json` documents aligned by
  `index`: action `kind`/`ref`/`detail` equality, `result.status` equality,
  `duration_ms` delta, `png_sha256` equality per display, and tree ref sets
  added/removed. Nothing about a diff is stored on disk.
