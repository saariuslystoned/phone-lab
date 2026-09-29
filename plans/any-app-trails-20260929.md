# Any-app trails (2026-09-29)

Bobby lifted the Cua-fixture-only policy (PR 10). This slice lets trails,
trees and taps work on any app, so phone-lab can prove UI changes in real
apps (first target: OpenClaw's Android debug build in its synthetic
screenshot-fixture mode on a Cua agent display).

Facts measured on the Fold today (cockpit probe, not yet in code):

- `am start -W --display <cua logical id> -n <pkg>/<activity> --ez K true
  --es K V` from the shell launches onto the Cua display (the virtual
  display is owned by uid 2000). Cua's `app launch` takes only package and
  activity, no intent extras, so this is the only way to pass extras.
- `wm size WxH -d <cua logical id>` resizes the Cua display live; the
  activity re-lays out in the same task. `wm size reset -d <id>` undoes it.
- When the Cua lease lapses the display and its task disappear.

## Changes

1. **Any package.** `trails.py`: drop the `ALLOWED_PACKAGES` membership
   checks (session `allow_apps` and `launch`); accept any well-formed
   Android package name (`^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$`),
   refuse anything else. `replay.record`: `allow_apps` is the packages the
   script launches (the fixture only when it launches nothing).
2. **Tree text for chosen apps.** `--app PKG` (repeatable) on `tree`,
   `serve`, `trail record`, `trail replay` extends the text and act packages
   passed to `TreeDumper` beyond the two defaults. `trail record/replay`
   also add the session's `allow_apps` automatically.
3. **Launch with intent extras.** `launch` gains optional `activity` (str)
   and `extras` (object: bool → `--ez`, int → `--ei`, str → `--es`).
   Script: `launch ai.openclaw.app.debug --activity ai.openclaw.app.MainActivity --ez openclaw.screenshotMode true --es openclaw.screenshotScene chat`
   (`--ez` value `true`/`false`, `--ei` integer). With `extras`, the runner
   does `am force-stop` (when fresh) then
   `am start -W --display <step display logical id> -n <pkg>/<activity> <extras>`
   (activity required when extras are given) and sets `target_id = None`;
   `action_detail` gets `via: "am"`. Without extras, the Cua launch path is
   unchanged (pass `activity` through to `cua-driver app launch --activity`
   when given).
4. **Taps without a Cua target.** When `target_id is None` (am launch), a
   `tap` injects `input -d <display> tap x y` at `tap_point(node)`;
   `action_detail` gets `via: "input"`. Cua snapshot+tap stays the path when
   Cua owns the task.
5. **`resize` action.** `{"kind": "resize", "size": "WxH" | "reset",
   "density": int | null}` → `wm size WxH -d <display>` and, when density is
   set, `wm density N -d <display>`; `reset` runs `wm size reset -d` and
   `wm density reset -d`. Script: `resize 1080x1920`, `resize 1920x1080
   --density 320`, `resize reset`. The runner remembers displays it resized
   and resets them in `close_session` before stopping the Cua session.
   Captures after a resize must use the new width/height (re-read
   inventory; do not cache sizes).
6. **Tap by label in scripts.** `tap label:"Jump to latest"` (also
   `set_text label:"…" "text"`): at record time resolve against the fresh
   tree: nodes whose `label_of` equals the text exactly; map each to its
   nearest clickable ancestor-or-self (for `set_text`: editable
   ancestor-or-self); dedupe; exactly one → use that node's ref and store
   `recorded`/`hint` as today plus `label` in the action; zero or several →
   step fails with a message naming the count. Replay uses the stored ref
   (and heal) as today; if the trail was never recorded (ref missing),
   fail with the existing re-record hint.

## Out of scope

Oracles for other apps, new predicate kinds, viewer UI changes, cua-driver
changes, anything touching display 0 by default (`--agent-only` remains the
caller's choice).

## Tests (fake backend, no device)

Parse/validate: package names (valid, invalid), launch with activity and
each extra type, extras without activity refused, `resize` forms, `tap
label:`. Runner: am launch shell args (display id, force-stop order,
extras), tap via input when `target_id` is None, resize issues `wm` and
`close_session` resets it, label resolution (unique, none, ambiguous,
clickable ancestor). CLI: `--app` reaches `TreeDumper` text/act packages.
Existing 136 tests stay green.

## Docs

`docs/feature-map.md` (slice 3 rows + a short "any app" note in Known
limits), `docs/trace-format.md` if it lists action kinds, README run lines
for `--app`.
