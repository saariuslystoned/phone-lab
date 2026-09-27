"""On-disk registry of Cua sessions that phone-lab created or drives."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SessionRecord:
    session_id: str
    label: str | None
    display_id: int | None
    package: str | None
    target_id: str | None
    state: str
    lease_remaining_ms: int
    lease_checked_at: float
    last_action: dict | None
    owner: str
    updated_at: float

    def lease_remaining_now(self, now: float | None = None) -> int:
        elapsed_ms = ((now if now is not None else time.time()) - self.lease_checked_at) * 1000
        return max(0, int(self.lease_remaining_ms - elapsed_ms))

    def to_json(self) -> dict:
        return asdict(self)


class Registry:
    def __init__(self, runs_dir: Path) -> None:
        self.dir = Path(runs_dir) / "sessions"

    def write(self, rec: SessionRecord) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{rec.session_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec.to_json(), indent=2) + "\n")
        os.replace(tmp, path)
        return path

    def load_all(self) -> list[SessionRecord]:
        records = []
        if not self.dir.is_dir():
            return records
        for path in sorted(self.dir.glob("*.json")):
            try:
                records.append(SessionRecord(**json.loads(path.read_text())))
            except (ValueError, TypeError, OSError):
                continue
        return records

    def by_display(self) -> dict[int, SessionRecord]:
        latest: dict[int, SessionRecord] = {}
        for rec in self.load_all():
            if rec.state != "active" or rec.display_id is None:
                continue
            if rec.display_id not in latest or rec.updated_at > latest[rec.display_id].updated_at:
                latest[rec.display_id] = rec
        return latest
