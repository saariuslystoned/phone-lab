# Upstream: Android skills — adaptive

- Source: https://github.com/android/skills/tree/main/jetpack-compose/adaptive
- Commit: `42dc2270e96032bd860bb94511e440aa00a43125` (2026-09-25)
- License: Apache License 2.0, copied unchanged as `LICENSE.txt` from the
  upstream repo root. Copyright Google LLC.
- Changes: none. `SKILL.md` and `references/` are byte-identical to the
  commit above (`diff -r` clean when vendored on 2026-10-04). Only this file
  and `LICENSE.txt` were added.

## Why it is here

The skill teaches an agent to make a Compose app adapt to phones,
foldables, tablets and desktop windows. phone-lab is where those apps get
proven on a real foldable: after an agent applies this skill to an app
(for example the Cua fixture apps, built in the Cua worktree), phone-lab
can show and drive both Fold panels and record the result.

## Refresh

```bash
git clone --depth 1 https://github.com/android/skills /tmp/android-skills
rsync -a --delete --exclude UPSTREAM.md --exclude LICENSE.txt \
  /tmp/android-skills/jetpack-compose/adaptive/ skills/adaptive/
cp /tmp/android-skills/LICENSE.txt skills/adaptive/LICENSE.txt
git -C /tmp/android-skills log -1 --format='%H %cs'   # update Commit above
```

Do not edit `SKILL.md` or `references/` in place; local changes go in a
separate skill so refreshes stay a clean copy.
