"""Numbered tap targets ("marks"): pick the tappable/labelled elements of a tree, number them, draw them.

No device access here: `select` works on a tree reply, `render` on PNG bytes, `resolve` on a mark list.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from .refs import assign_refs, is_interesting, label_of, tap_point

ROW_TOLERANCE_PX = 24
FULL_WINDOW_FRACTION = 0.9
PALETTE = ((230, 25, 75), (0, 130, 200), (60, 160, 60), (245, 130, 48), (145, 30, 180),
           (0, 128, 128), (170, 110, 40), (128, 0, 0))


def _area(b: list[int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def _intersect(a: list[int], b: list[int]) -> list[int]:
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def _actionable(node: dict) -> bool:
    return bool(node.get("clickable") or node.get("long_clickable") or node.get("editable") or node.get("checkable"))


def windows_for(tree: dict, include_system: bool = False) -> set[int]:
    """Window indices whose nodes may be marked.

    Default: the focused application window, else every application window (so systemui bars, the IME and
    overlays stay out). `include_system` keeps every window.
    """
    windows = tree.get("windows") or []
    if include_system or not windows:
        return {int(w.get("w", i)) for i, w in enumerate(windows)} | {n.get("w", 0) for n in tree.get("nodes", [])}
    apps = [w for w in windows if w.get("type_name") == "application"]
    focused = [w for w in apps if w.get("focused")]
    chosen = focused or apps
    return {int(w.get("w", windows.index(w))) for w in chosen}


def _window_bounds(tree: dict) -> dict[int, list[int]]:
    """Window bounds per window index; a window missing from the list takes its root node's bounds."""
    out = {}
    for i, w in enumerate(tree.get("windows") or []):
        b = w.get("bounds")
        if b and len(b) >= 4:
            out[int(w.get("w", i))] = list(b[:4])
    for node in tree.get("nodes", []):
        b = node.get("bounds")
        if node.get("parent") is None and node.get("w", 0) not in out and b and len(b) >= 4:
            out[node.get("w", 0)] = list(b[:4])
    return out


def _is_full_window(node: dict, win: list[int] | None) -> bool:
    if not win or _area(win) == 0:
        return False
    return _area(_intersect(node["bounds"], win)) >= FULL_WINDOW_FRACTION * _area(win)


def order(marks: list[dict], tolerance: int = ROW_TOLERANCE_PX) -> list[dict]:
    """Top-to-bottom rows (a box joins a row when its top is within `tolerance` of the row's first top),
    left-to-right inside a row; ties broken by ref so numbering does not depend on node order."""
    by_top = sorted(marks, key=lambda m: (m["bounds"][1], m["bounds"][0], m["ref"]))
    rows: list[list[dict]] = []
    for m in by_top:
        if rows and m["bounds"][1] - rows[-1][0]["bounds"][1] <= tolerance:
            rows[-1].append(m)
        else:
            rows.append([m])
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda m: (m["bounds"][0], m["bounds"][1], m["ref"])))
    return out


def select(tree: dict, include_system: bool = False, tolerance: int = ROW_TOLERANCE_PX) -> list[dict]:
    """Ordered marks `[{n, ref, label, class, bounds, tap, i, package}]` for one tree reply.

    A node is marked when it has a ref, sits in an allowed window (see `windows_for`), does not cover
    ≥ 90 % of its window, and is actionable (clickable, long-clickable, editable, checkable) or has a label.
    A label-only node inside a marked actionable ancestor is folded into that ancestor (its label is
    lent to the ancestor when the ancestor has none), so a Compose button and its text are one mark.
    """
    if not isinstance(tree.get("refs"), dict):
        assign_refs(tree)
    nodes = tree.get("nodes", [])
    by_index = {n.get("i", idx): n for idx, n in enumerate(nodes)}
    allowed = windows_for(tree, include_system)
    win_bounds = _window_bounds(tree)

    candidates: dict[int, dict] = {}
    for idx, node in enumerate(nodes):
        if not node.get("ref") or not is_interesting(node):
            continue
        if node.get("w", 0) not in allowed:
            continue
        if _is_full_window(node, win_bounds.get(node.get("w", 0))):
            continue
        label = label_of(node)
        if not (_actionable(node) or label):
            continue
        candidates[node.get("i", idx)] = node

    def actionable_ancestor(node: dict) -> int | None:
        p = node.get("parent")
        while p is not None and p in by_index:
            if p in candidates and _actionable(candidates[p]):
                return p
            p = by_index[p].get("parent")
        return None

    lent: dict[int, str] = {}
    for i, node in list(candidates.items()):
        if _actionable(node):
            continue
        anc = actionable_ancestor(node)
        if anc is not None:
            if not label_of(candidates[anc]) and anc not in lent:
                lent[anc] = label_of(node)
            del candidates[i]

    marks = []
    for i, node in candidates.items():
        x, y = tap_point(node)
        marks.append({
            "ref": node["ref"],
            "label": label_of(node) or lent.get(i, ""),
            "class": node.get("class") or "",
            "bounds": list(node["bounds"][:4]),
            "tap": [x, y],
            "i": i,
            "package": node.get("package"),
            "actionable": _actionable(node),
        })
    ordered = order(marks, tolerance)
    for n, m in enumerate(ordered, start=1):
        m["n"] = n
    return [{"n": m.pop("n"), **m} for m in ordered]


def resolve(marks: list[dict], token: str) -> dict | None:
    """'#N' → the mark numbered N; anything else is a ref. None when nothing matches; ValueError on '#x'."""
    token = token.strip()
    if token.startswith("#"):
        try:
            n = int(token[1:])
        except ValueError:
            raise ValueError(f"bad mark number {token!r}") from None
        return next((m for m in marks if m["n"] == n), None)
    return next((m for m in marks if m["ref"] == token), None)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render(png: bytes, marks: list[dict], crop_status_bar_px: int = 0) -> bytes:
    """PNG with a coloured box and a numbered badge per mark; the top `crop_status_bar_px` rows removed.

    Mark bounds are in display pixels and the screenshot is assumed 1:1 with them (true for `screencap -d`
    of an unrotated display). Badges never sit inside the cropped band.
    """
    image = Image.open(io.BytesIO(png)).convert("RGB")
    width, height = image.size
    crop = max(0, min(int(crop_status_bar_px or 0), height - 1))
    draw = ImageDraw.Draw(image)
    size = max(18, width // 36)
    font = _font(size)
    stroke = max(3, width // 360)
    for m in marks:
        colour = PALETTE[(m["n"] - 1) % len(PALETTE)]
        l, t, r, b = m["bounds"]
        draw.rectangle([l, t, r - 1, b - 1], outline=colour, width=stroke)
        text = str(m["n"])
        tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        pad = max(4, size // 5)
        bx = max(0, min(l, width - tw - 2 * pad))
        by = max(crop, min(t, height - th - 2 * pad))
        draw.rectangle([bx, by, bx + tw + 2 * pad, by + th + 2 * pad], fill=colour)
        draw.text((bx + pad - tb[0], by + pad - tb[1]), text, fill=(255, 255, 255), font=font)
    if crop:
        image = image.crop((0, crop, width, height))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
