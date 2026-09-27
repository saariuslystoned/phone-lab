# Publishing proof images

Every freeze under `runs/phone-lab-runs/` is git-ignored because the human
panels (display 0, the Fold's cover panel) are personal. The public proof
images live in a separate repo, `saari-co/public-oss-proof-assets`, and only
Bobby pushes there. This page is the route from a run to that repo.

## 1. Produce an agent-only composite

With a viewer running for the device (`python3 -m phonelab serve --model
"Pixel 10 Pro Fold" --port 0`, URL printed in the banner):

```bash
curl -s -X POST 'http://127.0.0.1:<port>/api/freeze?panels=agent'
```

The reply names the PNG and the manifest, written as
`runs/phone-lab-runs/<device-tag>/<YYYYMMDD>/freeze-agent-<HHMMSS>.{png,json}`.
The manifest carries `"panels_included": "agent"` and every panel in it has
`"role": "agent"`. When no Cua display is live the composite is an empty
canvas and `panels` is `[]`; that is not an error, it means there was
nothing publishable to capture. `panels=all` (or no query) is the ordinary
freeze and must never be published.

Without a device, `tests/test_compose.py` builds the same composite from
synthetic frames; the spec is `plans/proof-images.md`.

## 2. Check before handing it over

- `python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); assert m["panels_included"]=="agent" and all(p["role"]=="agent" for p in m["panels"]), m' <manifest.json>`
- Open the PNG and look at it. Agent panels show only the Cua synthetic
  apps (`ai.cua.fixture.notes`, `ai.cua.android.demo`); if anything else is
  on the agent display, do not publish.
- Run the serial scan from the proof run (reads serials from
  `adb devices -l`, never prints them) over the PNG's manifest; the
  manifest holds the model name and device tag only.

## 3. Hand-off to Bobby

Copy the PNG and its manifest into the run's proof directory
(`runs/phone-lab-runs/<device-tag>/<run>/`) and cite both by path in the
slice's `proof/<slice>/PROOF.md`. Do not commit the PNG here (`*.png` is
ignored outside `docs/`) and do not push to `saari-co/public-oss-proof-assets`
from an agent session. Bobby copies the file into that repo under
`phone-lab/<slice>/<YYYYMMDD>-<what>.png` and links it from the proof packet.
