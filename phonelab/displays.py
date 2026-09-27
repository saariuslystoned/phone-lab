"""Display model: join SurfaceFlinger ids (screencap) with logical ids (input, Cua) on uniqueId."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .adb import Adb

ROLE_ORDER = {"human": 0, "agent": 1, "ignored": 2}
AGENT_DISPLAY_NAMES = {"Cua agent"}


@dataclass
class Display:
    sf_id: str
    unique_id: str
    name: str
    kind: str
    logical_id: int | None
    width: int | None
    height: int | None
    state: str
    owner: str | None
    status_bar_px: int
    role: str


_SF_LINE = re.compile(r'^Display (\d+) \((HWC display \d+|Virtual display)\):(.*)$', re.M)
_DEVICE = re.compile(r'DisplayDeviceInfo\{"([^"]+)": uniqueId="([^"]+)", (\d+) x (\d+),(.*)$', re.M)
_LOGICAL_HEAD = re.compile(r'Logical Displays: size=(\d+)')
_LOGICAL_ID = re.compile(r'^\s*mDisplayId=(\d+)\s*$', re.M)
_BASE_INFO = re.compile(r'mBaseDisplayInfo=DisplayInfo\{"([^"]+)", displayId (\d+),[^\n]*?uniqueId "([^"]+)"')


def parse_surfaceflinger(text: str) -> list[dict]:
    """Entries of `dumpsys SurfaceFlinger --display-id`."""
    out = []
    for sf_id, kind, rest in _SF_LINE.findall(text):
        name = re.search(r'displayName="([^"]*)"', rest)
        unique = re.search(r'uniqueId="([^"]*)"', rest)
        physical = kind.startswith("HWC")
        out.append({
            "sf_id": sf_id,
            "kind": "physical" if physical else "virtual",
            "name": name.group(1) if name else "",
            "unique_id": f"local:{sf_id}" if physical else (unique.group(1) if unique else ""),
        })
    return out


def parse_dumpsys_display(text: str) -> dict[str, dict]:
    """Per uniqueId: name, size, state, owner, status-bar inset, logical id (from `dumpsys display`)."""
    info: dict[str, dict] = {}
    for name, unique, width, height, rest in _DEVICE.findall(text):
        state = re.search(r'\bstate (\w+)', rest)
        owner = re.search(r'\bowner (\S+) \(uid', rest)
        inset = re.search(r'insets=Rect\(\d+, (\d+) - \d+, \d+\)', rest)
        info[unique] = {
            "name": name, "width": int(width), "height": int(height),
            "state": state.group(1) if state else "UNKNOWN",
            "owner": owner.group(1) if owner else None,
            "status_bar_px": int(inset.group(1)) if inset else 0,
            "logical_id": None,
        }
    head = _LOGICAL_HEAD.search(text)
    if head:
        block = text[head.end():]
        id_lines = list(_LOGICAL_ID.finditer(block))
        for i, entry in enumerate(id_lines[: int(head.group(1))]):
            end = id_lines[i + 1].start() if i + 1 < len(id_lines) else len(block)
            base = _BASE_INFO.search(block, entry.end(), end)
            if not base:
                continue
            name, logical, unique = base.group(1), int(base.group(2)), base.group(3)
            info.setdefault(unique, {"name": name, "width": None, "height": None, "state": "UNKNOWN",
                                     "owner": None, "status_bar_px": 0, "logical_id": None})
            info[unique]["logical_id"] = logical
    return info


def _role(kind: str, name: str) -> str:
    if kind == "physical":
        return "human"
    return "agent" if name in AGENT_DISPLAY_NAMES else "ignored"


def join(sf: list[dict], dd: dict[str, dict]) -> list[Display]:
    displays = []
    for entry in sf:
        meta = dd.get(entry["unique_id"], {})
        name = meta.get("name") or entry["name"]
        displays.append(Display(
            sf_id=entry["sf_id"], unique_id=entry["unique_id"], name=name, kind=entry["kind"],
            logical_id=meta.get("logical_id"), width=meta.get("width"), height=meta.get("height"),
            state=meta.get("state", "UNKNOWN"), owner=meta.get("owner"),
            status_bar_px=meta.get("status_bar_px", 0) if entry["kind"] == "physical" else 0,
            role=_role(entry["kind"], name),
        ))
    displays.sort(key=lambda d: (ROLE_ORDER[d.role], d.logical_id if d.logical_id is not None else 1 << 30))
    return displays


def inventory(adb: Adb) -> list[Display]:
    return join(parse_surfaceflinger(adb.shell("dumpsys", "SurfaceFlinger", "--display-id")),
                parse_dumpsys_display(adb.shell("dumpsys", "display")))


def to_json(display: Display) -> dict:
    return asdict(display)
