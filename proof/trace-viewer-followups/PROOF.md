# Proof — trace viewer follow-ups

Run: 2026-09-27, cockpit Claude, worktree branch `claude/sad-dirac-826852`
on top of `f79fd92` (PRs 1 and 2 merged). Run bookkeeping is git-ignored
under `runs/phone-lab-runs/followups-20260927/` (`RUN/`). No device was
needed for anything here.

## What changed

| Item | Code | Tests |
|---|---|---|
| One shared response writer: `ResponseMixin._send/_json` in `phonelab/trace.py`; `ViewerHandler` (server.py) and `TraceHandler` inherit it; the `_send_resp`/`_send_json` fallbacks are gone | `phonelab/trace.py`, `phonelab/server.py` | `tests/test_trace.py::test_response_mixin_and_headers` |
| `handle_get` catch-all returns `{"error": "internal error"}` 500 and logs `trace error on <path>: <exc>` server-side instead of sending `str(exc)` (could carry absolute paths) | `phonelab/trace.py` | `tests/test_trace.py::test_internal_error_sanitization_and_logging` |
| `verify.py` reads `runs_dir` from `GET /api/state` (the device dir) by default; `--runs-dir` overrides; chosen path recorded in `numbers.runs_dir`; `resolve_runs_dir()` is pure | `skills/phone-lab-verify/scripts/verify.py`, `SKILL.md` | `tests/test_verify_runs_dir.py` (3) |

## Evidence

- `RUN/unittest-56.txt` — `Ran 56 tests … OK` (52 before, 4 new).
- `RUN/serial-scan.txt` — 0 hits for either attached serial in the checkout.
- Bridge proof: `~/.local/state/saarius-skills/antigravity-acp-delegation/runs/4843049f-144c-4984-86e7-ac8aa034f323/PROOF.md`
  (completed, cleanup completed, permission mode `approve-all` per readiness).

## Delegated versus written by the cockpit

- **Gemini 3.8 Flash** (`antigravity-acp`, job `4843049f`, one delegation,
  completed on the first attempt in about five minutes): all code and test
  changes above.
- **Cockpit (Claude)**: the brief, the review (diff accepted as-is apart
  from one reworded paragraph in `SKILL.md`), running the tests, this
  proof, the two-phone proof, and the worktree cleanup.

## Worktree cleanup (item 4)

Merged worktrees `youthful-bose-e4bb20` (PR 1) and `confident-agnesi-094d52`
(PR 2) were clean; their git-ignored run evidence was moved into the main
checkout as `runs/phone-lab-runs/shared-machine-20260927/` and
`runs/phone-lab-runs/slice-4-20260927/` (nothing overwritten), then
`git worktree remove` and `git branch -d` for both. `elegant-ishizaka-9134c0`
(slice 2) was not touched.
