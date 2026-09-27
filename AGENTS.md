# AGENTS.md — phone-lab contract for agents

phone-lab is a public MIT repo. These rules apply to every agent and worker.

## Device

- Test device: Bobby's registered Pixel 10 Pro Fold on USB ADB. Never print,
  log, commit, or publish its serial; show the model name instead.
- Inner panel is logical display 0; the cover panel is logical display 3.
  SurfaceFlinger ids (for `screencap -d`) and logical ids (for `input -d`,
  Cua `display_id`) are different namespaces; join them on `uniqueId`.
- Drive only Cua's synthetic apps (`ai.cua.fixture.notes`,
  `ai.cua.android.demo`) unless Bobby approves another app explicitly.
- Never change security settings, bypass Play Protect, or dismiss a PIN
  keyguard. A swipe-only keyguard may be dismissed with a swipe.
- Cua integration build: worktree `~/Developer/worktrees/cua-bobby-jellyware`
  (branch `bobby`); host CLI `libs/cua-driver/rust/target/debug/cua-driver`.

## Privacy

- `runs/`, `*-runs/`, and PNGs are git-ignored. Screens of the human's
  display are personal; keep them local. Sanitized proof images (status bar
  cropped) go to `saari-co/public-oss-proof-assets`.
- Never cite or copy from private repos into this one.

## Design origin

Ideas come from public write-ups of other tools; the implementation is our
own. Do not copy code from other projects into this repo.

## Work discipline

- One bounded slice per branch and worktree; the cockpit reviews and
  merges. Workers never self-merge.
- Every non-trivial claim needs proof: test output, a manifest, or a
  capture path. Long runs keep `STATE.md`, `events.jsonl`, `heartbeat`, and
  `PROOF.md` under `runs/phone-lab-runs/<run>/`.
- Keep the root to front-door files; see `REPO_HYGIENE.md`.
- Commit author: Bobby Bones <159389674+saariuslystoned@users.noreply.github.com>.
