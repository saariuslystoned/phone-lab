"""Element self-healing when replay encounters missing refs."""
from __future__ import annotations

from dataclasses import dataclass
import math

from . import refs


@dataclass
class HealResult:
    """Result of attempting to self-heal a missing ref."""

    status: str
    ref: str | None
    node: dict | None
    note: dict
    evidence: dict

    def to_json(self) -> dict:
        """Return status, ref, and note for embedding in trace step results."""
        return {
            "status": self.status,
            "ref": self.ref,
            "note": self.note,
        }


def _grid_centre(bounds: list[int]) -> list[int]:
    """Compute grid-rounded [cx, cy] centre from bounds."""
    cx, cy = refs.centre(bounds)
    return [refs.grid(cx), refs.grid(cy)]


def heal(
    missing_ref: str,
    recorded_tree: dict,
    current_tree: dict,
    *,
    max_distance_px: int = 120,
) -> HealResult:
    """Locate a matching node in current_tree for a missing recorded ref."""
    if "refs" not in current_tree:
        raise ValueError("current_tree must have a 'refs' key")

    evidence = {"recorded_tree": recorded_tree, "current_tree": current_tree}

    recorded = refs.find(recorded_tree, missing_ref)
    if recorded is None:
        note = {
            "kind": "failed",
            "reason": "no_candidate",
            "missing_ref": missing_ref,
            "ref": None,
            "max_distance_px": max_distance_px,
            "distance_px": None,
            "recorded": None,
            "current": None,
            "candidates": [],
            "message": "recorded ref not in recorded tree",
        }
        return HealResult(
            status="failed",
            ref=None,
            node=None,
            note=note,
            evidence=evidence,
        )

    rc = refs.label_of(recorded)
    rclass = recorded.get("class")
    rbounds = recorded.get("bounds") or [0, 0, 0, 0]
    rcentre = _grid_centre(rbounds)
    recorded_info = {
        "class": rclass,
        "label": rc,
        "centre": rcentre,
        "i": recorded.get("i"),
    }

    if not rc:
        note = {
            "kind": "failed",
            "reason": "no_candidate",
            "missing_ref": missing_ref,
            "ref": None,
            "max_distance_px": max_distance_px,
            "distance_px": None,
            "recorded": recorded_info,
            "current": None,
            "candidates": [],
            "message": "no interesting node shares the recorded label",
        }
        return HealResult(
            status="failed",
            ref=None,
            node=None,
            note=note,
            evidence=evidence,
        )

    candidates: list[tuple[dict, dict, int]] = []
    for idx, node in enumerate(current_tree.get("nodes", [])):
        if not refs.is_interesting(node):
            continue
        nlbl = refs.label_of(node)
        if nlbl != rc:
            continue
        nclass = node.get("class")
        tier = "moved" if nclass == rclass else "class_changed"
        nbounds = node.get("bounds") or [0, 0, 0, 0]
        ccentre = _grid_centre(nbounds)
        dx = ccentre[0] - rcentre[0]
        dy = ccentre[1] - rcentre[1]
        dist = int(round(math.hypot(dx, dy)))
        c_entry = {
            "ref": node.get("ref"),
            "tier": tier,
            "distance_px": dist,
            "class": nclass,
            "label": nlbl,
            "centre": ccentre,
            "i": node.get("i"),
        }
        candidates.append((c_entry, node, idx))

    if not candidates:
        note = {
            "kind": "failed",
            "reason": "no_candidate",
            "missing_ref": missing_ref,
            "ref": None,
            "max_distance_px": max_distance_px,
            "distance_px": None,
            "recorded": recorded_info,
            "current": None,
            "candidates": [],
            "message": "no interesting node shares the recorded label",
        }
        return HealResult(
            status="failed",
            ref=None,
            node=None,
            note=note,
            evidence=evidence,
        )

    tier_rank = {"moved": 0, "class_changed": 1}
    ranked = sorted(
        candidates,
        key=lambda item: (tier_rank[item[0]["tier"]], item[0]["distance_px"], item[2]),
    )
    ranked_entries = [c_entry for c_entry, _, _ in ranked]
    winner_entry, winner_node, _ = ranked[0]
    winner_tier = winner_entry["tier"]
    winner_d = winner_entry["distance_px"]

    if winner_d > max_distance_px:
        note = {
            "kind": "failed",
            "reason": "out_of_bound",
            "missing_ref": missing_ref,
            "ref": None,
            "max_distance_px": max_distance_px,
            "distance_px": winner_d,
            "recorded": recorded_info,
            "current": None,
            "candidates": ranked_entries[:20],
            "message": f"nearest candidate is {winner_d} px away, bound is {max_distance_px} px",
        }
        return HealResult(
            status="failed",
            ref=None,
            node=None,
            note=note,
            evidence=evidence,
        )

    tied = [
        c_entry
        for c_entry in ranked_entries
        if c_entry["tier"] == winner_tier and abs(c_entry["distance_px"] - winner_d) <= 10
    ]
    if len(tied) > 1:
        n = len(tied)
        note = {
            "kind": "failed",
            "reason": "ambiguous",
            "missing_ref": missing_ref,
            "ref": None,
            "max_distance_px": max_distance_px,
            "distance_px": winner_d,
            "recorded": recorded_info,
            "current": None,
            "candidates": ranked_entries[:20],
            "message": f"{n} candidates within 10 px of each other at {winner_d} px",
        }
        return HealResult(
            status="failed",
            ref=None,
            node=None,
            note=note,
            evidence=evidence,
        )

    current_info = {
        "class": winner_node.get("class"),
        "label": refs.label_of(winner_node),
        "centre": winner_entry["centre"],
        "i": winner_node.get("i"),
    }
    note = {
        "kind": "healed",
        "reason": winner_tier,
        "missing_ref": missing_ref,
        "ref": winner_entry["ref"],
        "max_distance_px": max_distance_px,
        "distance_px": winner_d,
        "recorded": recorded_info,
        "current": current_info,
        "candidates": ranked_entries[:20],
    }
    return HealResult(
        status="healed",
        ref=winner_entry["ref"],
        node=winner_node,
        note=note,
        evidence=evidence,
    )
