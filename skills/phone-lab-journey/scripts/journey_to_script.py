#!/usr/bin/env python3
"""Journey XML -> phone-lab trail script skeleton (stdlib only, no device).

Every journey <action> becomes a `# action N:` comment, a `name <sentence>`
line, and one compiled line. Lines the helper cannot compile safely start with
`TODO`, which `phonelab trail record` rejects ("unknown action kind") before it
touches the device; the agent replaces them with refs from a live tree.

    python3 skills/phone-lab-journey/scripts/journey_to_script.py journey.xml [-o out.trail.txt]

Exit 0 = skeleton written, 2 = malformed journey or app not allowed.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# Apps the skill may drive without Bobby's explicit approval (AGENTS.md).
ALLOWED_APPS = ("ai.cua.fixture.notes", "ai.cua.android.demo")
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
QUOTED_RE = re.compile(r"[\"“]([^\"”]+)[\"”]")


class JourneyError(Exception):
    """Malformed journey. Message is safe to print."""


@dataclass
class Journey:
    name: str
    description: str
    app: str | None
    actions: list[str]


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def parse_journey(xml_text: str) -> Journey:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise JourneyError(f"journey is not valid XML: {exc}") from None
    if root.tag != "journey":
        raise JourneyError(f"root element must be <journey>, got <{root.tag}>")
    name = _clean(root.get("name"))
    if not name:
        raise JourneyError("<journey> needs a name attribute")
    actions_el = root.find("actions")
    if actions_el is None:
        raise JourneyError("<journey> needs an <actions> element")
    actions = [_clean(a.text) for a in actions_el.findall("action")]
    if not actions:
        raise JourneyError("<actions> holds no <action>")
    for i, a in enumerate(actions, start=1):
        if not a:
            raise JourneyError(f"action {i} is empty")
    app_el = root.find("app")
    app = _clean(app_el.get("package")) if app_el is not None else None
    return Journey(name=name, description=_clean(root.findtext("description")), app=app or None, actions=actions)


def trail_name(journey_name: str) -> str:
    """A `phone-lab.trail.v1` name (NAME_RE) derived from the journey name."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", journey_name).strip("-._")
    return (slug or "journey")[:60]


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def compile_action(sentence: str, app: str | None) -> tuple[str, str]:
    """Return (line, note). Only unambiguous cases compile; the rest are TODO."""
    low = sentence.lower()
    first = low.split()[0].strip(",.:;") if low.split() else ""
    quoted = QUOTED_RE.findall(sentence)
    if first in ("launch", "open", "start") and app:
        keep = any(w in low for w in ("without restarting", "keep state", "already running"))
        return (f"launch {app}" + (" --keep" if keep else ""), "guessed: review")
    if first in ("check", "verify", "confirm", "assert", "ensure"):
        if len(quoted) == 1:
            return (f"wait_for text_present {_quote(quoted[0])}", "guessed: review")
        return ("TODO wait_for text_present <text from live tree>", "pick the predicate from the live tree")
    if first in ("tap", "click", "press", "select", "toggle"):
        return ("TODO tap <ref from live tree>", "resolve the ref from `phonelab tree <agent display>`")
    if first in ("type", "enter", "fill", "write", "set"):
        text = quoted[0] if quoted else "<text>"
        return (f"TODO set_text <ref from live tree> {_quote(text)}", "resolve the editable ref")
    if first in ("go", "navigate", "back") and "back" in low:
        return ("key KEYCODE_BACK", "guessed: review")
    if first in ("wait", "pause"):
        return ("TODO wait_for <predicate>", "prefer a predicate over sleep")
    return ("TODO <action>", "not a recognised UI verb; split or reword the action")


def skeleton(journey: Journey, source: str = "journey.xml", allow_other_app: bool = False) -> str:
    if journey.app is not None:
        if not PACKAGE_RE.fullmatch(journey.app):
            raise JourneyError(f"<app package> {journey.app!r} is not a package name")
        if journey.app not in ALLOWED_APPS and not allow_other_app:
            raise JourneyError(
                f"app {journey.app!r} is not one of {', '.join(ALLOWED_APPS)}; "
                "driving another app needs Bobby's explicit approval (--allow-other-app)"
            )
    out = [
        f"# compiled from {source} (journey {journey.name!r}) -> trail {trail_name(journey.name)!r}",
        "# refs: from a live `python3 -m phonelab tree <agent display>` read; record that here",
    ]
    if journey.description:
        out.append(f"# description: {journey.description}")
    for i, sentence in enumerate(journey.actions, start=1):
        line, note = compile_action(sentence, journey.app)
        out += ["", f"# action {i}: {sentence}", f"#   {note}", f"name {sentence}", line]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("journey", help="journey XML file")
    ap.add_argument("-o", "--output", help="write the script here (default stdout)")
    ap.add_argument("--allow-other-app", action="store_true",
                    help="allow an <app package> outside the Cua fixtures (needs Bobby's approval)")
    args = ap.parse_args(argv)
    path = Path(args.journey)
    try:
        text = skeleton(parse_journey(path.read_text()), source=path.name, allow_other_app=args.allow_other_app)
    except (JourneyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
