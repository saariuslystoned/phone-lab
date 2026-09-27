# Slice 5 — Self-heal (spec, core)

Date: 2026-09-27. Builds on slice-2 refs (`phonelab/refs.py`) and the
step shape in `docs/trace-format.md`. Slice 3 (trails, replay) is built in
parallel and is not on `main` yet; this slice defines the contract replay
will call and proves it on captured tree fixtures only. Device proof (one
healed replay and one loud failure on the Fold) waits for slice 3.

## Problem

A trail step records a ref (`e7f67h` for the fixture's increment button).
On replay the current tree may not contain that ref: the element moved
(a window resize, a fold posture change, a new banner above it) or its
class changed (a `Button` became an `ImageButton`). A ref is
`sha256(class|label|grid(cx)|grid(cy))`, so any of those changes produces
a different ref and `refs.find` returns `None`.

Self-heal answers one question: is there exactly one interesting node in
the current tree that is obviously the same element, close enough that
tapping it is safe? If yes, replay uses it and records that it did. If
not, replay fails loudly with both trees attached.

## Module: `phonelab/heal.py`

Standard library only. Pure: no I/O, no adb, no mutation of either tree.

```python
@dataclass
class HealResult:
    status: str            # "healed" | "failed"
    ref: str | None        # the winner's ref in current_tree when healed, else None
    node: dict | None      # the winning current node (same object as in current_tree) or None
    note: dict             # the trail note, shapes below
    evidence: dict         # {"recorded_tree": recorded_tree, "current_tree": current_tree} by reference

def heal(missing_ref: str, recorded_tree: dict, current_tree: dict,
         *, max_distance_px: int = 120) -> HealResult: ...
```

`HealResult.to_json()` returns `{"status", "ref", "note"}` only (never
the node or the trees) so a step result can embed it directly.

### Inputs

- `missing_ref`: the ref the step recorded.
- `recorded_tree`: the tree captured when the step was recorded, with
  `refs.assign_refs` already applied (the trail writer stores the tree
  after assignment, so `refs.find(recorded_tree, missing_ref)` works).
- `current_tree`: the tree just captured on replay, with
  `refs.assign_refs` already applied by the caller (replay assigns refs on
  every capture before it looks anything up). `heal` never mutates either
  tree; if `current_tree` has no `"refs"` key it raises `ValueError`. The
  caller should already have called `refs.find(current_tree, missing_ref)` and
  got `None`; `heal` does not re-check that and will happily return a
  distance-0 "moved" if called on an unchanged tree.
- `max_distance_px`: the bound, default 120 px. Replay exposes it as
  `--max-heal-px` (int, default 120; `0` disables healing so every missing
  ref fails `no_candidate`... see "Disabled" below).

### Algorithm

1. `recorded = refs.find(recorded_tree, missing_ref)`. If `None`, the
   result is `failed` / `no_candidate` with `candidates: []` and
   `message: "recorded ref not in recorded tree"`. The recorded fields in
   the note are `null`.
2. `rc = refs.label_of(recorded)`, `rclass = recorded["class"]`,
   `rcentre = (grid(cx), grid(cy))` of `refs.centre(recorded["bounds"])`.
3. Candidates: every node in `current_tree["nodes"]` with
   `refs.is_interesting(node)`, in node order. Each candidate gets a
   `tier`:
   - `"moved"` when `node["class"] == rclass` and `label_of(node) == rc`;
   - `"class_changed"` when `label_of(node) == rc` and the class differs;
   - otherwise it is not a candidate.
   Nodes whose label is `""` (redacted text or no label) never match a
   recorded label of `""`: an empty label is not evidence of identity, so
   when `rc == ""` the candidate set is empty and the result is
   `no_candidate`.
4. `distance_px` per candidate: euclidean distance between the
   grid-rounded centres, `round(math.hypot(dx, dy))` as an int.
5. Rank: tier first (`moved` before `class_changed`), then `distance_px`
   ascending, then node order. The winner is the first.
6. Ambiguity: if any other candidate in the same tier as the winner has
   `distance_px` within 10 px of the winner's (`abs(d - winner_d) <= 10`),
   the result is `failed` / `ambiguous`. Candidates in a lower tier never
   make the winner ambiguous.
7. Bound: if `winner.distance_px > max_distance_px`, the result is
   `failed` / `out_of_bound`.
8. Otherwise `healed`, with `reason` = the winner's tier and `ref` = the
   winner's `"ref"` from `assign_refs`.

Disabled: `max_distance_px = 0` still runs the search so the note is
informative, and only a distance-0 candidate heals (the element is at the
same grid cell but its class changed, which is exactly the
`class_changed` case at distance 0). Replay treats `--max-heal-px 0` as
"heal only in place".

### Note shapes (exact)

Every note has `kind` and `reason`; every field below is always present.
Centres are `[x, y]` lists of grid-rounded ints. `recorded` is `null`
only when the recorded ref was not found in the recorded tree.

Healed:

```json
{
  "kind": "healed",
  "reason": "moved",
  "missing_ref": "e7f67h",
  "ref": "k2m4xq",
  "max_distance_px": 120,
  "distance_px": 60,
  "recorded": {"class": "android.widget.Button", "label": "INCREMENT", "centre": [540, 260], "i": 6},
  "current":  {"class": "android.widget.Button", "label": "INCREMENT", "centre": [600, 260], "i": 6},
  "candidates": [
    {"ref": "k2m4xq", "tier": "moved", "distance_px": 60, "class": "android.widget.Button", "label": "INCREMENT", "centre": [600, 260], "i": 6}
  ]
}
```

`reason` is `"moved"` or `"class_changed"`. `candidates` lists every
candidate in ranked order (the winner first), capped at 20 entries.

Failed:

```json
{
  "kind": "failed",
  "reason": "out_of_bound",
  "missing_ref": "e7f67h",
  "ref": null,
  "max_distance_px": 120,
  "distance_px": 400,
  "recorded": {"class": "android.widget.Button", "label": "INCREMENT", "centre": [540, 260], "i": 6},
  "current": null,
  "candidates": [
    {"ref": "p9q2wz", "tier": "moved", "distance_px": 400, "class": "android.widget.Button", "label": "INCREMENT", "centre": [540, 660], "i": 6}
  ],
  "message": "nearest candidate is 400 px away, bound is 120 px"
}
```

`reason` is `"no_candidate"` (`distance_px: null`, `candidates: []`),
`"out_of_bound"` (`distance_px` = the nearest candidate's), or
`"ambiguous"` (`distance_px` = the winner's; `candidates` holds every
candidate, the tied ones first). `message` is one short human line and is
present only on failed notes.

### Evidence

`HealResult.evidence` is `{"recorded_tree": ..., "current_tree": ...}`,
the two dicts by reference. Replay writes them next to the step as
`tree-recorded-logical-<id>.json` and `tree-current-logical-<id>.json`
whenever heal ran, healed or not, so a failure always ships both captures
(the screenshots are already in `captures.before`).

## Trail-side contract (for slice 3)

Replay, per step with `action.ref`:

1. `node = refs.find(current_tree, ref)`; if found, act normally.
2. Else `res = heal(ref, recorded_tree, current_tree, max_distance_px=args.max_heal_px)`.
3. `res.status == "healed"`: act on `res.node` (tap at
   `refs.tap_point(res.node)`), and write the step result as

```json
"result": {
  "status": "healed",
  "message": "ref e7f67h healed: moved 60 px",
  "detail": {"healed": <the note>, "counter_before": 2, "counter_after": 3, ...}
}
```

   `action.ref` stays the recorded ref; `action.detail.ref_used` carries
   the healed ref and `action.detail.x/y` the tap point actually used.
4. `res.status == "failed"`: do not act. Write

```json
"result": {
  "status": "fail",
  "message": "ref e7f67h missing: out_of_bound (nearest candidate is 400 px away, bound is 120 px)",
  "detail": {"healed": <the note>}
}
```

   and add both trees to `trees`:
   `"heal": {"recorded": "tree-recorded-logical-98.json", "current": "tree-current-logical-98.json"}`.
   The run's `result.status` becomes `fail` per `docs/trace-format.md`.

CLI: `python3 -m phonelab replay ... --max-heal-px N` (int, default 120).
The value is echoed into `run.json` as `"heal": {"max_distance_px": 120}`.

## Tests: `tests/test_heal.py`

All on `tests/fixtures/tree_cua_fixture_captured.json` (the tree captured
on the Fold, increment ref `e7f67h` proven in slice 2). Load the fixture,
deep-copy it, mutate the copy, then call `assign_refs` on both (refs are
assigned after mutation so the current tree's refs and index are fresh):

| Case | Mutation of the increment node | Expected |
|---|---|---|
| unchanged | none | `refs.find` returns the node with ref `e7f67h`; heal is not called (and if it is, it returns `healed`/`moved`/`distance_px 0`) |
| moved | bounds shifted +60 px in x | `healed`, `reason "moved"`, `distance_px 60`, `ref` is the new node's ref and differs from `e7f67h`, `current.centre == [600, 260]` |
| class changed | `class` → `android.widget.ImageButton` | `healed`, `reason "class_changed"`, `distance_px 0` |
| out of bound | bounds shifted +400 px in y | `failed`, `reason "out_of_bound"`, `distance_px 400`, one candidate, `message` names 400 and 120 |
| no candidate | node deleted | `failed`, `reason "no_candidate"`, `distance_px null`, `candidates []` |
| ambiguous | node duplicated at x−60 and x+60, original removed | `failed`, `reason "ambiguous"`, two candidates both `distance_px 60` |
| bound is inclusive | shifted +120 px | `healed` |
| custom bound | shifted +60 px with `max_distance_px=50` | `failed` / `out_of_bound` |
| to_json | any | keys exactly `{"status", "ref", "note"}` |

Assert exact note fields (every key listed above is present; values as in
the table). Both trees must be unchanged after `heal` (compare each to a
deep copy taken just before the call). A current tree without `"refs"`
raises `ValueError`.

## Acceptance

- `python3 -m unittest discover -s tests -v` green, including the new
  tests.
- `heal` imports only `dataclasses`, `math`, `copy` (if any) and
  `phonelab.refs`.
- No serial anywhere in the diff.
- `docs/feature-map.md` slice-5 rows say `implemented (core), device proof
  after slice 3` with real function and test names.
- Device proof (roadmap: move the fixture's button and show one healed
  replay and one loud failure) is a slice-3 follow-up once replay exists.
