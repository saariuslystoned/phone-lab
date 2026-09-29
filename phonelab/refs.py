"""Cross-display content-stable element references."""
from __future__ import annotations

import hashlib
import math

GRID = 10
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 31 symbols, no 0/o/1/l/i
REF_LEN = 6


def label_of(node: dict) -> str:
    """text, else desc, else the id after '/' ("" when none); when text is redacted, "" (text_len is not a label)."""
    text = node.get("text")
    if text:
        return str(text)
    desc = node.get("desc")
    if desc:
        return str(desc)
    node_id = node.get("id")
    if node_id:
        s = str(node_id)
        if "/" in s:
            return s.split("/", 1)[1]
    return ""


def centre(bounds: list[int]) -> tuple[int, int]:
    cx = (bounds[0] + bounds[2]) // 2
    cy = (bounds[1] + bounds[3]) // 2
    return (cx, cy)


def grid(value: int, step: int = GRID) -> int:
    """round to nearest multiple of `step`, half up."""
    return int(math.floor(value / step + 0.5)) * step


def ref_key(node: dict) -> str:
    cls = node.get("class") or ""
    lbl = label_of(node)
    cx, cy = centre(node.get("bounds", [0, 0, 0, 0]))
    return f"{cls}|{lbl}|{grid(cx)}|{grid(cy)}"


def make_ref(key: str) -> str:
    """sha256(key) → integer → base-31 digits from ALPHABET, first REF_LEN symbols."""
    n = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest(), "big")
    digits = []
    base = len(ALPHABET)
    while n:
        digits.append(ALPHABET[n % base])
        n //= base
    ref = "".join(reversed(digits))
    return ref[:REF_LEN]


def is_interesting(node: dict) -> bool:
    """visible, non-empty bounds, and (clickable or long_clickable or editable or checkable or focusable or label_of(node) or id)."""
    if not node.get("visible"):
        return False
    bounds = node.get("bounds")
    if not bounds or len(bounds) < 4:
        return False
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return False
    return bool(
        node.get("clickable")
        or node.get("long_clickable")
        or node.get("editable")
        or node.get("checkable")
        or node.get("focusable")
        or label_of(node)
        or node.get("id")
    )


def assign_refs(tree: dict) -> dict:
    """adds "ref" (str) to every interesting node in place; collisions within one tree get "-2", "-3" … in node order;
    returns {"count": N, "refs": {ref: node_index}} and stores it as tree["refs"]."""
    seen: dict[str, int] = {}
    refs_map: dict[str, int] = {}
    nodes = tree.get("nodes", [])
    for idx, node in enumerate(nodes):
        if is_interesting(node):
            base = make_ref(ref_key(node))
            count = seen.get(base, 0) + 1
            seen[base] = count
            ref = base if count == 1 else f"{base}-{count}"
            node["ref"] = ref
            node_idx = node.get("i", idx)
            refs_map[ref] = node_idx
    ret = {"count": len(refs_map), "refs": refs_map}
    tree["refs"] = ret
    return ret


def find(tree: dict, ref: str) -> dict | None:
    refs = tree.get("refs")
    if isinstance(refs, dict):
        if "refs" in refs and isinstance(refs["refs"], dict):
            idx = refs["refs"].get(ref)
            if idx is not None and isinstance(idx, int):
                nodes = tree.get("nodes", [])
                if 0 <= idx < len(nodes):
                    return nodes[idx]
        elif ref in refs:
            idx = refs.get(ref)
            if idx is not None and isinstance(idx, int):
                nodes = tree.get("nodes", [])
                if 0 <= idx < len(nodes):
                    return nodes[idx]
    for node in tree.get("nodes", []):
        if node.get("ref") == ref:
            return node
    return None


def tap_point(node: dict) -> tuple[int, int]:
    """centre, clamped inside bounds."""
    bounds = node.get("bounds", [0, 0, 0, 0])
    cx, cy = centre(bounds)
    min_x, max_x = min(bounds[0], bounds[2]), max(bounds[0], bounds[2])
    min_y, max_y = min(bounds[1], bounds[3]), max(bounds[1], bounds[3])
    clamped_x = max(min_x, min(cx, max_x))
    clamped_y = max(min_y, min(cy, max_y))
    return (clamped_x, clamped_y)


def resolve_label(tree: dict, label: str, *, action_kind: str = "tap") -> list[dict]:
    """Find nodes matching label exactly, map each to nearest clickable (or editable) ancestor-or-self, and deduplicate."""
    nodes = tree.get("nodes", [])
    node_map = {n.get("i", idx): n for idx, n in enumerate(nodes)}
    matching = [n for n in nodes if label_of(n) == label]

    targets: list[dict] = []
    seen_indices: set[int] = set()

    for m in matching:
        curr: dict | None = m
        target: dict | None = None
        while curr is not None:
            if action_kind == "set_text":
                if curr.get("editable"):
                    target = curr
                    break
            else:
                if curr.get("clickable"):
                    target = curr
                    break
            p = curr.get("parent")
            if p is None or p not in node_map:
                break
            curr = node_map[p]

        if target is not None:
            idx = target.get("i", id(target))
            if idx not in seen_indices:
                seen_indices.add(idx)
                targets.append(target)

    return targets
