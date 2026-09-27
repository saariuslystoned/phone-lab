# phone-lab roadmap

Proof-sized slices, one branch and one worktree each, merged only after
Bobby's gate. Every slice ends with a `proof/<slice>/PROOF.md` that points at
git-ignored raw evidence under `runs/phone-lab-runs/` and at sanitized public
images in `saari-co/public-oss-proof-assets`.

Stack: see [ADR 001](../docs/adr-001-tech-stack.md).

## Slice 1 — Live multi-display viewer (2026-09-26, prototype proven 2026-09-27)

- [ ] `python3 -m phonelab serve` shows display 0, the cover panel, and every
      Cua virtual display side by side at ~1–2 fps with measured fps per panel.
      Proven for the side-by-side view and the per-panel fps readout; the Cua
      display measured 1.4–2.0 fps but display 0 measured 0.25 fps on the
      wallpaper home screen (4 MB PNG per frame), so the ~1–2 fps claim is not
      ticked. See `proof/slice-1-live-viewer/PROOF.md`.
- [x] Each Cua panel is labelled with session label, target package, lease
      remaining, and last action/result from the phone-lab session registry.
- [x] Freeze-frame button saves a labelled composite plus a JSON manifest to
      `runs/phone-lab-runs/<date>/`, status bar cropped from human panels.
- [x] `python3 -m phonelab cua demo` drives the synthetic fixture on a Cua
      display so the viewer can be proven live on the Fold.
- [x] Parser tests pass against captured `dumpsys` fixtures; the serial never
      appears in UI, logs, manifests, or tests.

Spec: [slice-1-live-viewer.md](slice-1-live-viewer.md).

## Slice 2 — Cross-display element refs (2026-09-27, proven on the Fold)

- [x] Read the accessibility tree of any display (shell-UID UiAutomation with
      `getWindowsOnAllDisplays`, connected without suppressing the human's
      services) and expose it as `GET /api/tree/<logical_id>`. Text and
      window titles are redacted outside the Cua apps.
- [x] Assign short content-stable refs (class, label, centre rounded to a 10 px
      grid, hashed) per display; show them as an overlay in the viewer.
- [x] Prove: ref for the fixture's increment button is identical across ten
      captures and survives a toast; tap-by-ref lands on a Cua display while a
      human types on display 0. See `proof/slice-2-element-refs/PROOF.md`.

Spec: [slice-2-element-refs.md](slice-2-element-refs.md).

## Slice 3 — Trails: record and replay

- Record a session as readable steps, each with the recorded actions
  (tap ref, set text, launch, wait-for predicate) and the display they ran on.
- Replay deterministically without an LLM; every step re-captures before and
  after and checks its predicate.
- Prove: record a five-step fixture trail, replay it three times, all green,
  with a trace directory per run.

## Slice 4 — Trace viewer

- Static page over `runs/phone-lab-runs/<run>/`: per-step screenshots of
  every display, element tree, action, result, timings.
- Prove: open a slice-3 run, step through it, diff two runs side by side.

Format the viewer reads and slice 3 writes: [docs/trace-format.md](../docs/trace-format.md).
Spec: [slice-4-trace-viewer.md](slice-4-trace-viewer.md).

## Slice 5 — Self-heal

- On replay, when a ref is missing, search the current tree for the nearest
  candidate (same class and label, moved; same label, class changed) within a
  bounded distance and repair the trail with a recorded "healed" note.
- Anything outside the bound fails loudly with both captures attached.
- Prove: move the fixture's button (fixture variant or window resize) and show
  one healed replay and one loud failure.

## Deferred

- Streaming capture (`screenrecord` or a device-side encoder) if 1–2 fps is
  ever the bottleneck.
- Driving apps beyond the synthetic fixture and demo needs Bobby's explicit
  approval per app.
