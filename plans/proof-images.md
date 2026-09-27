# Publishable proof images (spec)

Date: 2026-09-27. Follow-up to `plans/shared-machine-instances.md`.
Python 3.12, stdlib plus Pillow. No device needed for the tests.

## Problem

Every freeze composite contains the human panels (display 0, the cover
panel), so no freeze can be published as-is: `plans/roadmap.md` wants
sanitized images in `saari-co/public-oss-proof-assets`, and today the only
route is manual cropping.

## Decision

`POST /api/freeze?panels=agent` composes **only agent panels** (displays
whose `role == "agent"`). `panels=all` (the default, and the behaviour when
the query is absent) is unchanged. Any other value is a 400
`{"error": "panels must be agent or all"}`. No new CLI command: the route
is smaller and the viewer already has a Freeze button to extend later.

An agent-only composite never contains a human screen, so it can be
published after Bobby's review (see `docs/publishing-proof-images.md`).

## Contracts

```python
def compose(panels, device, now, sessions=None, panels_included: str = "all")
    # panels_included == "agent": tiles = displays with role == "agent" only
    # (ignored displays are still skipped in both modes)
    # manifest gains "panels_included": "all" | "agent"

def freeze(manager, device, runs_dir, panels_included: str = "all") -> dict
    # file stem: freeze-<HHMMSS> for all, freeze-agent-<HHMMSS> for agent
    # returns {"image", "manifest", "panels", "panels_included"}
```

Manifest (schema id stays `phone-lab.freeze.v1`, the field is additive):

```json
{"schema": "phone-lab.freeze.v1", "created_at": "…", "device": {…},
 "image": "freeze-agent-101500.png", "panels_included": "agent",
 "panels": [ {"role": "agent", …} ]}
```

With `panels=agent` and no agent display live, the composite is the empty
canvas the existing code already draws for zero tiles and `panels` is
`[]`; the response still succeeds so a caller can tell "no agent display"
from an error. `recent_freezes` must list both stems (`freeze-*.json`
already matches `freeze-agent-*.json`; the sort key regex must accept the
`agent-` infix).

## Tests (`tests/test_compose.py`, existing fixtures)

- `compose(..., panels_included="agent")` over `[HUMAN, AGENT, OFF, IGNORED]`
  yields one manifest panel with `role == "agent"`, `panels_included ==
  "agent"`, and no panel named `Inner Display` or `Outer Display`.
- Default call still yields three panels and `panels_included == "all"`.
- `freeze(..., panels_included="agent")` writes `freeze-agent-HHMMSS.png`
  plus `.json`, the manifest carries `panels_included: "agent"` and one
  panel, and `recent_freezes` lists it newest first next to an all-panels
  freeze.
- HTTP: `POST /api/freeze?panels=agent` on a `bind_viewer(port=0)` server
  with the stub manager returns `panels_included == "agent"`;
  `?panels=bogus` returns 400.

## Out of scope

Pushing to `saari-co/public-oss-proof-assets` (Bobby does that by hand),
changes to the viewer page, and cropping inside agent panels.
