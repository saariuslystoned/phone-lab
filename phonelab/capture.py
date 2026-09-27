"""Per-display capture threads and the capture manager that joins displays to sessions."""
from __future__ import annotations

import io
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from PIL import Image

from . import displays as display_model
from .adb import Adb, AdbError
from .displays import Display
from .sessions import Registry

JPEG_QUALITY = 80
ERROR_BACKOFF_S = 1.0


class CaptureError(Exception):
    """Failure to capture or decode a frame."""


class FrameSource:
    """Tiny protocol/base for per-display frame sources."""

    label: str = "unknown"

    def next_frame(self) -> tuple[bytes, bytes, int, int] | None:
        """Return (png, jpeg, width, height) for a new frame, None when no new frame yet.

        Raises CaptureError on failure.
        """
        raise NotImplementedError

    def stop(self) -> None:
        """Stop any background resources. No-op by default."""


class ScreencapSource(FrameSource):
    """Wraps adb screencap for a single display."""

    label: str = "screencap"

    def __init__(self, adb: Adb, display: Display | Callable[[], Display],
                 max_height: int = 1000, on_failure: Callable[[], None] | None = None) -> None:
        self.adb = adb
        self._display = display
        self.max_height = max_height
        self.on_failure = on_failure

    def _get_display(self) -> Display:
        return self._display() if callable(self._display) else self._display

    def next_frame(self) -> tuple[bytes, bytes, int, int] | None:
        display = self._get_display()
        try:
            png = self.adb.screencap(display.sf_id)
        except AdbError as exc:
            raise CaptureError(str(exc)) from exc
        if png is None:
            if self.on_failure is not None:
                self.on_failure()
            raise CaptureError("screencap returned no PNG")
        try:
            jpeg, width, height = _decode(png, self.max_height)
        except Exception as exc:
            raise CaptureError(f"decode failed: {exc}") from exc
        return png, jpeg, width, height

    def stop(self) -> None:
        pass


@dataclass
class Frame:
    """The latest capture of one display: full-resolution PNG plus a downscaled JPEG preview."""

    seq: int
    captured_at: float
    capture_ms: int
    png: bytes
    jpeg: bytes
    width: int
    height: int
    fps: float = 0.0


def _decode(png: bytes, max_height: int) -> tuple[bytes, int, int]:
    """JPEG preview bytes (RGB, height-capped) plus the PNG's own width and height."""
    image = Image.open(io.BytesIO(png))
    width, height = image.size
    rgb = image.convert("RGB")
    if height > max_height:
        rgb = rgb.resize((max(1, round(width * max_height / height)), max_height), Image.LANCZOS)
    out = io.BytesIO()
    rgb.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), width, height


class DisplayCapture(threading.Thread):
    """One capture loop per display. Skips OFF displays; records the last error."""

    def __init__(self, adb: Adb, display: Display, max_height: int = 1000, interval: float = 0.0,
                 on_failure: Callable[[], None] | None = None,
                 source: FrameSource | None = None) -> None:
        super().__init__(name=f"capture-{display.unique_id}", daemon=True)
        self.adb = adb
        self.display = display
        self.max_height = max_height
        self.interval = interval
        self.on_failure = on_failure
        self.source = source if source is not None else ScreencapSource(
            self.adb, lambda: self.display, self.max_height, self.on_failure
        )
        self.frame: Frame | None = None
        self.error: str | None = None
        self.fps = 0.0
        self.captures = 0
        self.errors = 0
        self._seq = 0
        self._times: deque[float] = deque(maxlen=10)
        self._halt = threading.Event()  # not `_stop`: that name is a Thread method used by join()

    def stop(self) -> None:
        self._halt.set()
        self.source.stop()

    def _fail(self, message: str) -> None:
        self.error = self.adb.redact(message)
        self.errors += 1
        self._halt.wait(ERROR_BACKOFF_S)

    def run(self) -> None:
        while not self._halt.is_set():
            if self.display.state == "OFF":
                self.error = "display off"
                self._halt.wait(ERROR_BACKOFF_S)
                continue
            started = time.time()
            try:
                frame_data = self.source.next_frame()
            except CaptureError as exc:
                self._fail(str(exc))
                continue
            if frame_data is None:
                self._halt.wait(0.05)
                continue
            png, jpeg, width, height = frame_data
            self._seq += 1
            self._times.append(started)
            if len(self._times) >= 2:
                span = self._times[-1] - self._times[0]
                self.fps = (len(self._times) - 1) / span if span > 0 else 0.0
            self.frame = Frame(seq=self._seq, captured_at=started, capture_ms=int((time.time() - started) * 1000),
                               png=png, jpeg=jpeg, width=width, height=height, fps=self.fps)
            self.error = None
            self.captures += 1
            if self.interval > 0:
                remaining = self.interval - (time.time() - started)
                if remaining > 0:
                    self._halt.wait(remaining)


class CaptureManager:
    """Owns one DisplayCapture per non-ignored display and re-discovers displays periodically."""

    def __init__(self, adb: Adb, registry: Registry, rediscover_every: float = 2.0, max_height: int = 1000,
                 source_factory: Callable[[Display], FrameSource | None] | None = None) -> None:
        self.adb = adb
        self.registry = registry
        self.rediscover_every = rediscover_every
        self.max_height = max_height
        self.source_factory = source_factory
        self.inventory_error: str | None = None
        self.inventories = 0
        self._threads: dict[str, DisplayCapture] = {}  # keyed by unique_id: SurfaceFlinger ids change under Cua
        self._order: list[Display] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._discovery: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._discovery = threading.Thread(target=self._discover_loop, name="rediscover", daemon=True)
        self._discovery.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            thread.stop()
        if self._discovery is not None:
            self._discovery.join(timeout=5)
        for thread in threads:
            thread.join(timeout=5)

    def _discover_loop(self) -> None:
        while not self._stop.is_set():
            self.rediscover()
            self._wake.wait(self.rediscover_every)
            self._wake.clear()

    def wake(self) -> None:
        """Ask for an immediate re-inventory (called from capture threads on failure)."""
        self._wake.set()

    def rediscover(self) -> None:
        """One inventory pass: start, update, or stop capture threads to match the device."""
        try:
            found = display_model.inventory(self.adb)
        except AdbError as exc:
            self.inventory_error = self.adb.redact(str(exc))
            return
        self.inventory_error = None
        self.inventories += 1
        wanted = [d for d in found if d.role != "ignored"]
        with self._lock:
            self._order = wanted
            seen = {d.unique_id for d in wanted}
            for display in wanted:
                thread = self._threads.get(display.unique_id)
                if thread is None:
                    source = self.source_factory(display) if self.source_factory else None
                    thread = DisplayCapture(self.adb, display, self.max_height, on_failure=self.wake, source=source)
                    self._threads[display.unique_id] = thread
                    thread.start()
                else:
                    thread.display = display  # picks up a new sf_id or an ON/OFF flip; seq continues
            for unique_id in list(self._threads):
                if unique_id not in seen:
                    self._threads.pop(unique_id).stop()

    def _snapshot(self) -> list[tuple[Display, DisplayCapture | None]]:
        with self._lock:
            return [(d, self._threads.get(d.unique_id)) for d in self._order]

    def _lookup(self, key: str) -> DisplayCapture | None:
        """Find a capture thread by SurfaceFlinger id, unique id, or `logical-<n>`."""
        with self._lock:
            for thread in self._threads.values():
                d = thread.display
                if key in (d.sf_id, d.unique_id) or (d.logical_id is not None and key == f"logical-{d.logical_id}"):
                    return thread
        return None

    def frame(self, key: str) -> Frame | None:
        thread = self._lookup(key)
        return thread.frame if thread else None

    def frames(self) -> list[tuple[Display, Frame | None]]:
        return [(d, t.frame if t else None) for d, t in self._snapshot()]

    def sessions_now(self, now: float | None = None) -> dict[int, dict]:
        """Active registry records keyed by logical display id, with the decayed lease added."""
        now = time.time() if now is None else now
        return {lid: {**rec.to_json(), "lease_remaining_now_ms": rec.lease_remaining_now(now)}
                for lid, rec in self.registry.by_display().items()}

    def state(self) -> dict:
        """The `displays` part of GET /api/state plus manager diagnostics."""
        now = time.time()
        sessions = self.sessions_now(now)
        out = []
        for display, thread in self._snapshot():
            frame = thread.frame if thread else None
            entry = {
                "sf_id": display.sf_id, "unique_id": display.unique_id, "logical_id": display.logical_id, "name": display.name,
                "kind": display.kind, "role": display.role, "state": display.state,
                "width": display.width, "height": display.height,
                "seq": frame.seq if frame else None,
                "captured_at": frame.captured_at if frame else None,
                "capture_ms": frame.capture_ms if frame else None,
                "fps": round(thread.fps, 2) if thread else 0.0,
                "error": thread.error if thread else None,
                "captures": thread.captures if thread else 0,
                "errors": thread.errors if thread else 0,
                "session": None,
                "source": thread.source.label if thread else None,
            }
            if display.role == "agent":
                match = sessions.get(display.logical_id) if display.logical_id is not None else None
                entry["session"] = match if match else {"state": "unknown"}
            out.append(entry)
        return {"displays": out, "inventory_error": self.inventory_error, "inventories": self.inventories}
