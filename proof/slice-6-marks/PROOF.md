# Slice 6 — Numbered tap targets ("marks"): proof

Date: 2026-10-06. Device: Pixel 10 Pro Fold (USB ADB, phone open, inner
panel = logical 0, 2076x2152, status bar 160 px; cover panel OFF as
logical 3). Branch `slice-6-marks`, worktree
`~/Developer/worktrees/phone-lab-slice-6-marks-20261006`. The Fold is
shared; every device step ran under `/tmp/phone-lab-fold-device.lock`
(held 10:06:12–10:07:02 EDT, then briefly for the serial grep).

Raw evidence (git-ignored, local):
`runs/phone-lab-runs/pixel-10-pro-fold/slice-6-marks-20261006-100528/`
(`events.jsonl`, `heartbeat`, `STATE.md`, `PROOF.md`, `preflight.txt`,
`marks-cap{1,2}.{png,json,stdout.json,time}`, `tap{1,2,3}.json`,
`tap-stale.json`, `taps.txt`, `counter-before.json`).

## Unit tests

`python3 -m unittest discover -s tests`: 165 tests, OK
(`tests/test_marks.py`: 18).

## Device

Preflight (`preflight.txt`): awake, `isKeyguardShowing=false`;
`ai.cua.fixture.notes/.MainActivity` started on display 0 with
`am start -W --display 0`; counter read through the tree: `Count: 0`,
focused package `ai.cua.fixture.notes`.

`python3 -m phonelab marks 0 --model "Pixel 10 Pro Fold"`, twice:

| capture | marks | #1 | #2 | #3 | tree ms | screencap ms | render ms | total ms | wall s |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | `xhxqua` editor | `b4cmbt` INCREMENT | `hy53ge` Count: 0 | 59 | 915 | 43 | 1019 | 1.96 |
| 2 | 3 | `xhxqua` editor | `b4cmbt` INCREMENT | `hy53ge` Count: 0 | 67 | 1025 | 42 | 1135 | 2.11 |

Numbering identical across both captures. The overlay
(`marks-cap1.png`, status bar cropped, 2076x1992) was viewed by the
worker: badge 2 sits on the INCREMENT button, so the tap target was read
from the image as `#2`. No systemui node was marked (default filter).

`python3 -m phonelab tap 0 '#2' --expect-ref b4cmbt`, three times
(`taps.txt`):

| tap | rc | tap point | tree ms | `input` ms | total ms | wall ms | counter after |
|---|---|---|---|---|---|---|---|
| 1 | 0 | (1038, 307) | 80 | 231 | 311 | 1247 | Count: 1 |
| 2 | 0 | (1038, 307) | 97 | 163 | 260 | 1171 | Count: 2 |
| 3 | 0 | (1038, 307) | 69 | 172 | 241 | 1095 | Count: 3 |

Count rose by exactly 3. Wall time includes Python start, device pick,
jar check and treedump connect.

Stale guard: `tap 0 '#2' --expect-ref xhxqua` → exit 3,
`"refused": "stale"`, `"#2 is now ref b4cmbt, expected xhxqua; re-run
marks"` (`tap-stale.json`); counter stayed `Count: 3`.

## Hygiene

- Serial grep (`grep -rl -a "$(adb get-serialno)"`) over the worktree
  and the run directory: no matches.
- No PNG or capture committed; overlays live only under `runs/`.
- Only `ai.cua.fixture.notes` was driven. No security setting, keyguard,
  or APK touched.

## Notes

- The fixture title (behind the status bar on the inner panel) got no
  mark on display 0: 3 marks there versus 4 on the captured Cua-display
  fixture. Not investigated further (likely not visible-to-user in the
  tree).
- The editor's and counter's refs follow their text (slice-2 ref key);
  numbers stay put because ordering is by geometry, but `--expect-ref`
  against those two goes stale when their text changes. INCREMENT's ref
  is text-stable.
- `marks` pushed this worktree's treedump jar (sha256 `0a9a3bee…`),
  replacing the jar another worker had on the device; `TreeDumper`
  re-pushes on sha mismatch, so either side self-repairs.
