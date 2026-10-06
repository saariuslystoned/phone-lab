# Proof — phone-lab Journeys skill

Run: 2026-10-06, worker on branch `skills/phone-lab-journey`, worktree
`~/Developer/worktrees/phone-lab-journey-skill-20261006` (base `origin/main`
8f08595). Device: Bobby's registered Pixel 10 Pro Fold on USB (the only phone
attached), shared with another worker through
`/tmp/phone-lab-fold-device.lock` (held only for the device steps, released
after). Every phonelab command was pinned with `--model "Pixel 10 Pro Fold"`
and ran `--agent-only`, so display 0 was never captured. Raw evidence is
git-ignored under `runs/phone-lab-runs/pixel-10-pro-fold/` (cited as `DEV/`);
bookkeeping (STATE.md, events.jsonl, heartbeat, PROOF.md, logs, scripts) under
`DEV/journey-skill-20261006-100424/` (cited as `RUN/`). No image committed.

## Verdict

The skill works end to end on the Fold: the example journey
(`skills/phone-lab-journey/examples/fixture-increment.xml`, four sentences)
was compiled from a live tree read into a trail script, recorded (4/4 ok),
and replayed twice with no LLM (2/2 pass, 8/8 steps ok). Each step carries
its original sentence through the trail, `run.json`, `step.json`, the log
lines and the trace viewer.

## Step names: carry-through check and the one core change

- Already carried before this change: `name` lines → `Step.name` →
  trail JSON → `step.json.name` and `run.json.steps[].name` (`RunWriter`) →
  the replay log line and the trace viewer's step header (`trace.html`
  renders `step.name`).
- Gap found: `parse_script_line` ran `name` lines through `shlex.split`,
  which dropped quotes (`reads "Count: 2".` → `reads Count: 2.`) and raised
  "No closing quotation" on an apostrophe (`don't`). Journey sentences hit
  both. Fix (`phonelab/trails.py`): a `name` line is taken verbatim
  (whitespace collapsed; one enclosing quote pair still stripped so
  `name "tap first"` keeps working). Tests:
  `tests/test_trails.py::test_name_line_keeps_sentence_verbatim`,
  `tests/test_replay.py::test_journey_sentence_names_carry_into_record_and_replay_runs`.
- No viewer change: the viewer already shows the name.

## Device run

| Phase | Command (abridged) | Result | Evidence |
|---|---|---|---|
| Live tree | `cua demo --no-taps --duration 120`, `phonelab tree 14` | ok; Cua agent display logical 14; INCREMENT `e7f67h` (Button, `…:id/increment`), counter `kyvprg` "Count: 0"; session stopped (`cleanup released`) | `RUN/live-tree-agent.json`, `RUN/live-tree-session.log` |
| Compile | `journey_to_script.py journey.xml` → resolve 2 `TODO tap` lines with `e7f67h` | 4 lines, each under `name <sentence>` | `RUN/skeleton.trail.txt`, `RUN/fixture-increment.trail.txt` (committed as the example) |
| Record | `trail record … --name fixture-increment --agent-only` | exit 0, 4/4 ok, 17.8 s; predicates derived `fixture_counter 0/1/2`, step 4 explicit `text_present "Count: 2"` | `DEV/fixture-increment-record-20261006-100510/`, `RUN/record.log` |
| Replay ×2 | `trail replay RUN/trails/fixture-increment.json --times 2 --agent-only` | exit 0, 2/2 pass, 15.1 s and 15.1 s, every step ok | `DEV/fixture-increment-replay-20261006-100530/`, `DEV/fixture-increment-replay-20261006-100546/`, `RUN/replay.log` |
| Results | `journey_results.py --journey … <3 run dirs>` | exit 0, four `✅` actions, Overall PASS | `RUN/journey-result.md` |
| Trace viewer | `phonelab trace --port 0 --runs-dir DEV`, `GET /api/runs/<replay 2>/steps/0..3` | names are the four sentences verbatim, quotes kept | `RUN/trace-api-observation.txt` |
| Cleanup | `phonelab inventory`; registry | no agent display left; all four session records `stopped` | `RUN/events.jsonl` |

Per-step times (record / replay 1 / replay 2, ms): launch 4293 / 4121 /
4196; tap 4561 / 4460 / 4734; tap again 4355 / 4480 / 4463; verify 1995 /
2010 / 1705.

The Cua shell runtime was up; no restart was needed.

## Automated tests

`python3 -m unittest discover -s tests` → `Ran 159 tests … OK` (147 on
`origin/main`; `tests/test_journey_skill.py` adds 10, plus 1 each in
`test_trails.py` and `test_replay.py`). No device needed.

## Privacy

Serial grep (value read from `adb devices -l` into a variable, never
echoed) over the checkout (excluding `.git`) and `RUN/` plus the three run
directories: no match. All device logs were piped through a `sed` that
replaces the serial.

## Open risks

- The skeleton helper's verb heuristics are deliberately narrow; anything
  else stays `TODO` for the agent. A sentence with two quoted strings under
  "verify" is left `TODO`.
- Refs are content hashes: a journey compiled at one Cua size/density may
  need self-heal or a recompile at another.
- The results helper groups steps by exact sentence (or `[k/n]` suffix);
  editing a sentence in the journey without recompiling shows the action as
  `❌ no compiled step`.
