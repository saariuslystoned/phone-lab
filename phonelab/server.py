"""HTTP server: the viewer page, the JSON state API, JPEG frames, and freeze-frame composites."""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import io
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageDraw, ImageFont

from .adb import Adb
from .capture import CaptureManager, Frame
from .displays import Display
from .presence import ViewerPresence
from .sessions import Registry

UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"
SCHEMA = "phone-lab.freeze.v1"
PANEL_HEIGHT = 1000
GUTTER = 24
TITLE_BAND = 150
FOOTER_BAND = 40
CANVAS_BG = (18, 18, 22)
PLACEHOLDER_BG = (48, 48, 54)
TEXT = (235, 235, 240)
MUTED = (170, 170, 180)
GREY = (140, 140, 150)
BLUE = (90, 160, 255)


class PortInUse(OSError):
    """`host:port` already has a listener. The message names the port."""


def _iso(now: float) -> str:
    return dt.datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")


def _hms(at: float) -> str:
    return dt.datetime.fromtimestamp(at).strftime("%H:%M:%S.") + f"{int((at % 1) * 1000):03d}"


def _session_lines(session: dict | None) -> list[str]:
    """The agent panel's session strip, split so it fits a 1080x1920 panel scaled to height 1000."""
    if not session or session.get("state") == "unknown":
        return ["Cua session: unknown to phone-lab"]
    label = session.get("label") or str(session.get("session_id") or "")[:8]
    lease_s = (session.get("lease_remaining_now_ms") or 0) / 1000
    action = session.get("last_action") or {}
    return [f"Cua {label} · {session.get('package')}",
            f"lease {lease_s:.0f}s · last {action.get('kind', '-')} {action.get('result', '-')}"]


def _fit(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    """Truncate with an ellipsis so the text stays inside its panel."""
    if font.getlength(text) <= max_width:
        return text
    while text and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return text + "…"


def _panel_image(display: Display, frame: Frame | None) -> tuple[Image.Image | None, int, int]:
    """Scaled panel image (or None for a placeholder), its width, and the cropped status-bar pixels."""
    if frame is None:
        width = display.width or 1080
        height = (display.height or 1920) - (display.status_bar_px if display.kind == "physical" else 0)
        return None, max(1, round(width * PANEL_HEIGHT / max(1, height))), 0
    source = Image.open(io.BytesIO(frame.png)).convert("RGB")
    crop = 0
    if display.kind == "physical" and 0 < display.status_bar_px < source.height:
        crop = display.status_bar_px
        source = source.crop((0, crop, source.width, source.height))
    scaled = source.resize((max(1, round(source.width * PANEL_HEIGHT / source.height)), PANEL_HEIGHT), Image.LANCZOS)
    return scaled, scaled.width, crop


def compose(panels: list[tuple[Display, Frame | None]], device: dict, now: float,
            sessions: dict[int, dict] | None = None) -> tuple[Image.Image, dict]:
    """Pure composite builder: one labelled panel per display (ignored displays skipped) plus a manifest."""
    sessions = sessions or {}
    font_title = ImageFont.load_default(size=30)
    font_small = ImageFont.load_default(size=22)
    tiles = [(d, f, *_panel_image(d, f)) for d, f in panels if d.role != "ignored"]
    total_width = GUTTER + sum(width + GUTTER for _, _, _, width, _ in tiles) if tiles else 3 * GUTTER + 400
    height = GUTTER + TITLE_BAND + PANEL_HEIGHT + GUTTER + FOOTER_BAND + GUTTER
    canvas = Image.new("RGB", (total_width, height), CANVAS_BG)
    draw = ImageDraw.Draw(canvas)
    manifest_panels = []
    x = GUTTER
    for display, frame, image, width, crop in tiles:
        top = GUTTER + TITLE_BAND
        draw.text((x, GUTTER), _fit(f"{display.name} · logical {display.logical_id} · {display.width}x{display.height}",
                                    font_title, width), fill=TEXT, font=font_title)
        if frame is not None:
            line2 = f"seq {frame.seq} · captured {_hms(frame.captured_at)} · {frame.capture_ms} ms · {frame.fps:.1f} fps"
        else:
            line2 = "display off" if display.state == "OFF" else "no frame yet"
        draw.text((x, GUTTER + 42), _fit(line2, font_small, width), fill=MUTED, font=font_small)
        session = None
        if display.role == "agent":
            session = sessions.get(display.logical_id) if display.logical_id is not None else None
            for i, line in enumerate(_session_lines(session)):
                draw.text((x, GUTTER + 74 + 28 * i), _fit(line, font_small, width), fill=BLUE, font=font_small)
        if image is not None:
            canvas.paste(image, (x, top))
        else:
            draw.rectangle([x, top, x + width - 1, top + PANEL_HEIGHT - 1], fill=PLACEHOLDER_BG, outline=(80, 80, 88))
            draw.text((x + 20, top + PANEL_HEIGHT // 2 - 16), line2, fill=GREY, font=font_title)
        manifest_panels.append({
            "sf_id": display.sf_id, "logical_id": display.logical_id, "name": display.name, "role": display.role,
            "seq": frame.seq if frame else None,
            "captured_at": frame.captured_at if frame else None,
            "capture_ms": frame.capture_ms if frame else None,
            "width": frame.width if frame else display.width,
            "height": frame.height if frame else display.height,
            "png_sha256": hashlib.sha256(frame.png).hexdigest() if frame else None,
            "cropped_status_bar_px": crop,
            "session": session if display.role == "agent" else None,
        })
        x += width + GUTTER
    footer = f"phone-lab freeze · {device.get('model')} · Android {device.get('android_release')} · {_iso(now)}"
    draw.text((GUTTER, height - GUTTER - FOOTER_BAND + 6), footer, fill=GREY, font=font_small)
    manifest = {"schema": SCHEMA, "created_at": _iso(now), "device": device, "image": None, "panels": manifest_panels}
    return canvas, manifest


def freeze(manager: CaptureManager, device: dict, runs_dir: Path) -> dict:
    """Compose the current frames and write `<runs_dir>/<YYYYMMDD>/freeze-<HHMMSS>.png` plus `.json`."""
    now = time.time()
    image, manifest = compose(manager.frames(), device, now, manager.sessions_now(now))
    day_dir = Path(runs_dir) / dt.datetime.fromtimestamp(now).strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    stem = "freeze-" + dt.datetime.fromtimestamp(now).strftime("%H%M%S")
    candidate, n = stem, 1
    while (day_dir / f"{candidate}.png").exists() or (day_dir / f"{candidate}.json").exists():
        candidate, n = f"{stem}-{n}", n + 1
    png_path, json_path = day_dir / f"{candidate}.png", day_dir / f"{candidate}.json"
    image.save(png_path, format="PNG")
    manifest["image"] = png_path.name
    json_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return {"image": str(png_path), "manifest": str(json_path), "panels": len(manifest["panels"])}


def recent_freezes(runs_dir: Path, limit: int = 10) -> list[dict]:
    """The most recent freeze manifests (parsed), newest first; corrupt files are skipped."""
    def key(path: Path) -> tuple[str, str, int]:
        match = re.fullmatch(r"freeze-(\d{6})(?:-(\d+))?", path.stem)
        return (path.parent.name, match.group(1) if match else path.stem, int(match.group(2) or 0) if match else 0)

    paths = sorted(Path(runs_dir).glob("*/freeze-*.json"), key=key, reverse=True)
    out = []
    for path in paths[:limit]:
        try:
            out.append(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
    return out


def bind_viewer(host: str = "127.0.0.1", port: int = 8791, adb: Any = None,
                manager: Any = None, device: dict | None = None,
                runs_dir: Path | str = Path("runs/phone-lab-runs")) -> ViewerServer:
    """Bind the viewer socket before anything touches the device; port 0 picks a free port."""
    try:
        return ViewerServer((host, port), adb=adb, manager=manager, device=device, runs_dir=Path(runs_dir))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise PortInUse(
                f"port {port} on {host} is already in use (another phone-lab viewer or another agent's server?); "
                f"pass --port 0 to pick a free port or --port N for another one"
            ) from exc
        raise


def viewer_url(server: ViewerServer) -> str:
    """The URL with the port actually bound (matters for `--port 0`)."""
    host, real_port = server.server_address[:2]
    return f"http://{host}:{real_port}/"


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    presence: ViewerPresence | None = None

    def __init__(self, address: tuple[str, int], adb: Any = None, manager: Any = None,
                 device: dict | None = None, runs_dir: Path | str = Path("runs/phone-lab-runs"),
                 presence: ViewerPresence | None = None) -> None:
        super().__init__(address, ViewerHandler)
        self.adb = adb
        self.manager = manager
        self.device = device or {}
        self.runs_dir = Path(runs_dir)
        self.presence = presence

    def service_actions(self) -> None:
        super().service_actions()
        if self.presence is not None:
            self.presence.beat()


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "phone-lab/0.1"
    server: ViewerServer

    def log_message(self, fmt: str, *args) -> None:  # one line per request, never a serial
        print(self.server.adb.redact(f"{time.strftime('%H:%M:%S')} {fmt % args}"), flush=True)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, UI_PATH.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            state = self.server.manager.state() if self.server.manager else {}
            viewers = self.server.presence.others() if self.server.presence else []
            self._json({"device": self.server.device, "server_time": time.time(),
                        "runs_dir": str(self.server.runs_dir), "viewers": viewers, **state})
        elif path == "/api/freezes":
            self._json(recent_freezes(self.server.runs_dir))
        elif path.startswith("/frame/") and path.endswith(".jpg"):
            frame = self.server.manager.frame(unquote(path[len("/frame/"):-len(".jpg")])) if self.server.manager else None
            if frame is None:
                self._json({"error": "no frame yet"}, 404)
            else:
                self._send(200, frame.jpeg, "image/jpeg", {"X-Frame-Seq": str(frame.seq)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/freeze":
            try:
                self._json(freeze(self.server.manager, self.server.device, self.server.runs_dir))
            except Exception as exc:  # report, never crash the server thread
                self._json({"error": self.server.adb.redact(f"freeze failed: {exc}")}, 500)
        else:
            self._json({"error": "not found"}, 404)


def serve(adb: Adb, registry: Registry, host: str = "127.0.0.1", port: int = 8791,
          runs_dir: Path = Path("runs/phone-lab-runs"), max_height: int = 1000) -> int:
    """Run the viewer until Ctrl-C; capture threads start immediately."""
    runs_dir = Path(runs_dir)
    try:
        server = bind_viewer(host, port, adb=adb, runs_dir=runs_dir)
    except PortInUse as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    manager: CaptureManager | None = None
    presence: ViewerPresence | None = None
    try:
        device_tag = getattr(adb, "tag", "unknown")
        device = {"model": adb.model, "device_tag": device_tag, **adb.props()}
        server.device = device

        manager = CaptureManager(adb, registry, max_height=max_height)
        server.manager = manager
        manager.start()

        real_port = server.server_address[1]
        presence = ViewerPresence(runs_dir, host=host, port=real_port, device_tag=device_tag)
        server.presence = presence
        presence.start()

        for other in presence.others():
            age = int(round(other.get("heartbeat_age_s", 0)))
            print(f"another viewer for {device_tag} is running: {other['url']} (pid {other['pid']}, heartbeat {age} s ago)", flush=True)
        print(f"phone-lab viewer on {viewer_url(server)} · {device['model']} · Android {device['android_release']} · runs {runs_dir}", flush=True)

        server.serve_forever(poll_interval=0.5)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if presence is not None:
            presence.stop()
        server.server_close()
        if manager is not None:
            manager.stop()
        print("phone-lab viewer stopped", flush=True)
