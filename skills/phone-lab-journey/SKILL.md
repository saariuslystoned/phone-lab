---
name: phone-lab-journey
description: Turn a plain-English phone test (a Journeys-style XML file) into a phone-lab trail once, with an agent reading the live element tree, then replay it deterministically with no LLM and write a Journeys-style results file. Triggers - "run this journey", "compile a journey", "plain-English phone test", "turn these steps into a trail".
---

# phone-lab journey

A journey is a test written as sentences. The agent compiles it **once**:
it reads the live element tree, turns each sentence into one trail-script
line (or a few), and keeps the sentence as the step name. `trail record`
runs it live and writes the trail; `trail replay --times N` repeats it with
no LLM. Every run directory, log line and trace-viewer step shows the
original sentence.

Use it when a check should be repeatable: a regression test for the Cua
fixture, a demo you will replay many times, or a test somebody wrote in
English. For a one-off look at the screen, read `phonelab tree` directly.

## Rules (same boundary as phone-lab-verify)

- **Serial**: never print, log, or commit it. Pin commands with
  `--model "Pixel 10 Pro Fold"` (or `--serial S` from a variable you never
  echo). Pipe logs through a `sed` that replaces the serial.
- **Apps**: drive only `ai.cua.fixture.notes` and `ai.cua.android.demo`.
  Anything else needs Bobby's explicit approval;
  `scripts/journey_to_script.py` refuses other `<app package>` values unless
  `--allow-other-app`.
- **Displays**: journeys run on the Cua agent display. Use `--agent-only`
  so display 0 (the human's screen) is never captured; nothing from
  display 0 is committed or published.
- **Device**: never touch security settings, Play Protect, or a PIN
  keyguard. Stop and report when a human step is needed. Do not install
  APKs. If the device is shared, hold the agreed lock around device steps.
- **Proof**: run directories live under git-ignored
  `runs/phone-lab-runs/<device-tag>/`. Track only text proof that cites
  them by path. No PNGs in git.

## Journey format

Borrowed from Android's Journeys format, plus one optional phone-lab
element:

```xml
<journey name="fixture-increment">
  <description>What the journey checks, in one sentence.</description>
  <app package="ai.cua.fixture.notes"/>          <!-- optional, phone-lab only -->
  <actions>
    <action>Launch the Synthetic Notes Fixture from a fresh start.</action>
    <action>Tap the INCREMENT button.</action>
    <action>Verify the counter text reads "Count: 1".</action>
  </actions>
</journey>
```

- An `<action>` is one UI interaction. If it lists several ("search for
  soda and add the first result"), compile it into several steps named
  `<sentence> [1/2]`, `<sentence> [2/2]`; the results helper groups them
  back into one action.
- An action that starts with **check** or **verify** is an expectation: it
  compiles to `wait_for <predicate>` and does not touch the screen. Several
  expectations in one sentence → several `wait_for` steps, `[k/n]` named.
- An action that is not a UI interaction makes the journey malformed: stop
  and report it; do not guess.

Example: [examples/fixture-increment.xml](examples/fixture-increment.xml),
compiled: [examples/fixture-increment.trail.txt](examples/fixture-increment.trail.txt).

## Workflow

1. **Read the journey.** Check the app is allowed and every action is a UI
   interaction or an expectation.
2. **Skeleton** (optional, removes typing):
   `python3 skills/phone-lab-journey/scripts/journey_to_script.py journey.xml -o journey.trail.txt`.
   It writes `name <sentence>` before every line, compiles `launch` and
   quoted `verify … "text"` sentences, and leaves `TODO …` lines for the
   rest. `trail record` rejects a `TODO` line before touching the device.
3. **Start a Cua session on the agent display** without taps:
   `python3 -m phonelab cua demo --model M --no-taps --duration 120 &`
   (driver from `PHONELAB_CUA_DRIVER`). The log prints
   `logical display <N>`. If session create fails because the phone
   rebooted and the Cua shell runtime is gone, restart only that runtime
   the way the Cua worktree's deploy script launches it, log it as an
   event, and report it.
4. **Read the live tree**: `python3 -m phonelab tree <N> --model M > live-tree.json`.
   Then stop the demo with `kill <pid>` (SIGTERM stops the session).
5. **Translate each action** into a trail-script line
   (grammar: `plans/slice-3-trails.md`, "Record script"), keeping the
   `name <sentence>` line above it:

   | Sentence shape | Line |
   |---|---|
   | Launch / open the app (fresh) | `launch <pkg>` (`--keep` to keep state) |
   | Tap / click / press X | `tap <ref of X>`; `tap label:"X"` when the label is stable and unique |
   | Type T into field F | `set_text <ref of F> "T"` |
   | Go back | `key KEYCODE_BACK` |
   | Verify / check text T is shown | `wait_for text_present "T"` |
   | Verify element X is shown / gone | `wait_for ref_present <ref>` / `wait_for ref_absent <ref>` |
   | Verify the fixture counter is N | `wait_for fixture_counter N` |

   Refs come from the live tree only, never from coordinates or memory.
   Write the source of each ref (display, class, resource id) in a comment.
   Use `expect <predicate>` on an action line when the sentence carries
   its own expectation ("tap X so the dialog closes").
6. **Record**: `python3 -m phonelab trail record journey.trail.txt --name <trail> --model M --agent-only [--trails-dir DIR]`.
   Exit 0 writes `<trail>.json`; a failing step means the compile is wrong
   or the app is: fix the line, not the predicate.
7. **Replay**: `python3 -m phonelab trail replay <trail>.json --times 2 --model M --agent-only`.
   Each run creates and stops its own Cua session.
8. **Results**:
   `python3 skills/phone-lab-journey/scripts/journey_results.py --journey journey.xml <record-run> <replay-runs…> -o journey-result.md`
   (exit 0 = all runs passed, 1 = something failed, 2 = bad input). Run
   directories are `runs/phone-lab-runs/<device-tag>/<trail>-<record|replay>-<ts>/`.
   Open them in `python3 -m phonelab trace` to see each sentence beside
   its screenshots and tree.

## Results file

`journey_results.py` writes the Journeys layout: `# Journey: <name>`, a
run table, then `### Action: <sentence> ✅|❌` per action with the
compiled line(s), every run's status and time, and comments (failure
messages, heals). ✅ = every run passed the action; ❌ = some run failed it
or no step carries the sentence; no mark = not evaluated in every run
because an earlier action failed. Treat failures as test results: report
them, keep debugging minimal, put fix suggestions in the comments.
