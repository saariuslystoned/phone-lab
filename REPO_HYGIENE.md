# Repo hygiene

Keep the root onboarding-grade.

Allowed root files: `README.md`, `AGENTS.md`, `REPO_HYGIENE.md`, `LICENSE`,
`.gitignore`, and package metadata once needed.

- `phonelab/` — the application package (Python, stdlib plus Pillow).
- `tests/` — unit tests and captured text fixtures (no serials, no personal
  screen content).
- `docs/` — durable depth: ADRs (`adr-NNN-*.md`), display model, formats.
- `plans/` — dated roadmap and per-slice specs.
- `proof/<slice>/PROOF.md` — tracked, text-only proof packets that point at
  raw evidence by path.
- `runs/phone-lab-runs/` — git-ignored run output: captures, freezes,
  session registry, manifests, STATE/PROOF for long runs.

Do not commit PNGs outside `docs/`, and never commit a capture of a personal
screen anywhere.
