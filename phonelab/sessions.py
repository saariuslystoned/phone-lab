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
    device_tag: str | None = None

    def lease_remaining_now(self, now: float | None = None) -> int:
        elapsed_ms = ((now if now is not None else time.time()) - self.lease_checked_at) * 1000
        return max(0, int(self.lease_remaining_ms - elapsed_ms))

    def to_json(self) -> dict:
        return asdict(self)


class Registry:
    def __init__(self, runs_root: Path, device_tag: str | None = None) -> None:
        self.device_tag = device_tag
        runs_root = Path(runs_root)
        if device_tag is None:
            self.dir = runs_root / "sessions"
            self.legacy_dir: Path | None = None
        else:
            self.dir = runs_root / device_tag / "sessions"
            self.legacy_dir = runs_root / "sessions"

    def write(self, rec: SessionRecord) -> Path:
        if rec.device_tag is None:
            rec.device_tag = self.device_tag
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{rec.session_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec.to_json(), indent=2) + "\n")
        os.replace(tmp, path)
        return path

    def load_all(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        dirs = [self.dir]
        if self.legacy_dir is not None and self.legacy_dir != self.dir:
            dirs.append(self.legacy_dir)
        seen_ids: set[str] = set()
        for d in dirs:
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.json")):
                try:
                    data = json.loads(path.read_text())
                    rec = SessionRecord(**data)
                except (ValueError, TypeError, OSError):
                    continue
                if self.device_tag is not None and rec.device_tag is not None and rec.device_tag != self.device_tag:
                    continue
                if rec.session_id in seen_ids:
                    continue
                seen_ids.add(rec.session_id)
                records.append(rec)
        return records

    def by_display(self) -> dict[int, SessionRecord]:
        latest: dict[int, SessionRecord] = {}
        for rec in self.load_all():
            if rec.state != "active" or rec.display_id is None:
                continue
            if rec.display_id not in latest or rec.updated_at > latest[rec.display_id].updated_at:
                latest[rec.display_id] = rec
        return latest
