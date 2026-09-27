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

Status: just started.

## License

MIT
