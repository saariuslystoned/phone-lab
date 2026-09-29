# Any-app trails on a real app: OpenClaw PR proofs (2026-09-29)

First use of phone-lab on an app other than Cua's fixture: before/after
proof for three open openclaw/openclaw Android PRs on the Pixel 10 Pro Fold.
OpenClaw ran as a locally built debug APK in its synthetic
screenshot-fixture mode (`--ez openclaw.screenshotMode true`), so no
Gateway and no personal data were involved. Evidence lives under
`runs/phone-lab-runs/openclaw-pr-proof-20260929/` (git-ignored); this
packet cites it.

## What phone-lab did

| PR | phone-lab features used | Result |
|---|---|---|
| 143466 flat large-screen nav | trail `resize` (nine live `wm size -d` resizes of one Cua display, same task), `launch` with extras, `set_text label:`, label heal, trees | One trail recorded on the base build replayed 18/18 on base and head; per-step trees give the nav mode and the 360 dp composer offset at every size (`pr143466/nav-table.txt`) |
| 155918 Jump to latest | `swipe`, `tap label:`, label heal, trees | Trail recorded on base (header icon) replayed on head, where the tap healed by label onto the new floating button (`pr155918-table.txt`) |
| 147282 chat switching | trees + `input -d` from the helper in the run dir; not trails | Transition timing needed `screenrecord`, which cannot record agent displays, so this ran on physical display 0 |

Trace viewer (`serve --runs-dir … --app …`, `/trace`) listed and rendered
the OpenClaw runs. The run diff was not exercised (browser pane closed).

## Bugs found by the real app, fixed on this branch

1. **Stale trees.** treedump's long-lived UiAutomation connection returned
   the closed-drawer tree after OpenClaw's drawer reopened; a fresh
   `uiautomator dump` at the same moment saw the drawer (29 KB, "Product
   notes", "Close navigation menu"). Fix: `UiAutomation.clearCache()`
   before every read. A/B on the same flow: old jar missed the drawer, new
   jar returned it (82 nodes). `events.jsonl` id `stale-tree`.
2. **Restart without push.** `TreeDumper.tree()` restarted the device
   process without pushing, so a caller that skipped `start()` ran an old
   jar. The restart now pushes and retries the connect (another tool ran
   `uiautomator dump` on the Fold every few seconds). Unit test added.
3. **Fixture oracle leaking into other apps.** Record mode derived
   `fixture_counter` predicates from the installed Cua fixture while
   driving OpenClaw, so the first `oc-155918-jump` record failed with
   "counter stayed at 0". The oracle is now used only when the session app
   is the fixture. Unit test added.
4. **Heal could not follow a moved Compose text field.** The geometric heal
   matches on the node's own label, which an OpenClaw composer field does
   not have (its placeholder is a child), so replaying the base trail on
   the head build failed at "type a draft" (`no_candidate`). Label steps now
   re-resolve by label first. Unit test added; both head replays show
   `healed` with reason `label`.

Also rejected from the worker's diff: a loosened `CuaDriver.call` error
check (accepted replies with no status and ignored exit codes); restored.

## Limits met (documented in `docs/feature-map.md`)

- `screenrecord` records physical displays only.
- The trace tree drops `scrollable` and `editable`.
- One UiAutomation client per phone; foreign dumps force reconnects.
- No oracle for other apps; predicates must be explicit.

## Checks

- `python3 -m unittest discover -s tests`: 147 tests, OK.
- `grep -rn "$(adb -s <fold> get-serialno)"` over the checkout: no hits
  (run before commit).
