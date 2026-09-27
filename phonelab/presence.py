"""Multi-viewer presence tracking on a shared machine: heartbeats, PID checks, pruning."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

SCHEMA = "phone-lab.viewer.v1"
HEARTBEAT_EVERY_S = 5.0
STALE_AFTER_S = 30.0


def _default_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


class ViewerPresence:
    def __init__(self, device_dir: Path, host: str, port: int, device_tag: str,
                 pid: int | None = None, clock: Callable[[], float] = time.time,
                 pid_alive: Callable[[int], bool] | None = None) -> None:
        self.device_dir = Path(device_dir)
        self.host = host
        self.port = port
        self.device_tag = device_tag
        self.pid = os.getpid() if pid is None else pid
        self.clock = clock
        self.pid_alive = pid_alive if pid_alive is not None else _default_pid_alive
        self.started_at: float | None = None
        self.last_beat_at: float = 0.0
        self.dir = self.device_dir / "viewers"
        self.path = self.dir / f"{self.pid}.json"

    def _write_file(self, now: float) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = {
            "schema": SCHEMA,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}/",
            "device_tag": self.device_tag,
            "started_at": self.started_at if self.started_at is not None else now,
            "heartbeat_at": now,
        }
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp, self.path)

    def start(self) -> None:
        now = self.clock()
        self.started_at = now
        self.last_beat_at = now
        self._write_file(now)

    def beat(self, force: bool = False) -> None:
        now = self.clock()
        if force or (now - self.last_beat_at >= HEARTBEAT_EVERY_S):
            self.last_beat_at = now
            if self.started_at is None:
                self.started_at = now
            self._write_file(now)

    def others(self) -> list[dict]:
        if not self.dir.is_dir():
            return []
        now = self.clock()
        live: list[dict] = []
        for path in sorted(self.dir.glob("*.json")):
            if path.name == self.path.name:
                continue
            try:
                data = json.loads(path.read_text())
                pid = int(data["pid"])
                heartbeat_at = float(data["heartbeat_at"])
                url = str(data["url"])
            except (ValueError, KeyError, TypeError, OSError):
                continue
            if pid == self.pid:
                continue
            age = now - heartbeat_at
            alive = self.pid_alive(pid)
            if not alive or age >= STALE_AFTER_S:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            live.append({
                "pid": pid,
                "url": url,
                "heartbeat_age_s": round(max(0.0, age), 1),
            })
        return live

    def stop(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
