# phone-lab

A personal, open-source lab for experimenting with Android phones and the agents that drive them.

Built from scratch around a few needs that existing tools don't cover:

- **See every display at once.** The human's screen and every agent display (for example
  [Cua](https://github.com/trycua/cua)'s private virtual displays) side by side, live, labelled.
- **Element references.** Stable references to on-screen elements on *any* display, not just display 0.
- **Trails.** Record a session as readable steps plus recorded actions, and replay it deterministically.
- **A trace viewer.** Per-step screenshots, element trees, and results for every run.
- **Self-heal.** Replays that notice small drift and repair it, failing loudly otherwise.

Foldables and multi-display devices are first-class. Proof is part of the product: every run
produces evidence a human and an agent can both check.

## Status

Slice 1 (live multi-display viewer) is a working prototype, proven on a
Pixel 10 Pro Fold with a live Cua session: display 0, the (off) cover panel
and the Cua agent display side by side with measured fps per panel, the Cua
panel labelled with session label, package, lease and last tap result, and a
freeze-frame button that writes a labelled composite plus a JSON manifest.
Display 0 runs well under 1 fps on busy screens; see the known limits in
[docs/feature-map.md](docs/feature-map.md).

Run it (Python 3.12 plus Pillow, one authorized device on USB):

```bash
python3 -m unittest discover -s tests -v
python3 -m phonelab inventory
python3 -m phonelab serve                      # http://127.0.0.1:8791/
python3 -m phonelab cua demo --driver /path/to/cua-driver --duration 300 --tap-every 8
```

Other agents' emulators and phones can share the machine: phone-lab never
picks an `emulator-*` device on its own, and with several phones attached
it asks you to choose with `--serial S`, `--model "Pixel 10 Pro Fold"`, or
`ANDROID_SERIAL`. Errors name models, never serials.

- [docs/adr-001-tech-stack.md](docs/adr-001-tech-stack.md) — the stack decision
- [docs/feature-map.md](docs/feature-map.md) — features, modules, tests, proof, status
- [plans/roadmap.md](plans/roadmap.md) — slices 1–5
- [skills/phone-lab-verify/SKILL.md](skills/phone-lab-verify/SKILL.md) — the verification loop

## License

MIT
