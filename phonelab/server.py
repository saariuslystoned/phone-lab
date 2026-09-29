"""HTTP server: the viewer page, the JSON state API, JPEG frames, and freeze-frame composites."""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import io
import json
import re
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from PIL import Image, ImageDraw, ImageFont

from . import trace
from .adb import Adb
from .capture import CaptureManager, Frame
from .cua import CuaDriver, CuaError
from .displays import Display
from .presence import ViewerPresence
from .refs import assign_refs, find, tap_point
from .sessions import Registry
from .trace import ResponseMixin
from .tree import TreeDumper, TreeError, packages_with

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
            sessions: dict[int, dict] | None = None,
            panels_included: str = "all") -> tuple[Image.Image, dict]:
    """Pure composite builder: one labelled panel per display (ignored displays skipped) plus a manifest."""
    if panels_included not in ("agent", "all"):
        raise ValueError("panels must be agent or all")
    sessions = sessions or {}
    font_title = ImageFont.load_default(size=30)
    font_small = ImageFont.load_default(size=22)
    if panels_included == "agent":
        tiles = [(d, f, *_panel_image(d, f)) for d, f in panels if d.role == "agent"]
    else:
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
    manifest = {"schema": SCHEMA, "created_at": _iso(now), "device": device, "image": None, "panels_included": panels_included, "panels": manifest_panels}
    return canvas, manifest


def freeze(manager: CaptureManager, device: dict, runs_dir: Path,
           panels_included: str = "all") -> dict:
    """Compose the current frames and write `<runs_dir>/<YYYYMMDD>/freeze-<HHMMSS>.png` plus `.json`."""
    now = time.time()
    image, manifest = compose(manager.frames(), device, now, manager.sessions_now(now), panels_included=panels_included)
    day_dir = Path(runs_dir) / dt.datetime.fromtimestamp(now).strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    prefix = "freeze-agent-" if panels_included == "agent" else "freeze-"
    stem = prefix + dt.datetime.fromtimestamp(now).strftime("%H%M%S")
    candidate, n = stem, 1
    while (day_dir / f"{candidate}.png").exists() or (day_dir / f"{candidate}.json").exists():
        candidate, n = f"{stem}-{n}", n + 1
    png_path, json_path = day_dir / f"{candidate}.png", day_dir / f"{candidate}.json"
    image.save(png_path, format="PNG")
    manifest["image"] = png_path.name
    json_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return {
        "image": str(png_path),
        "manifest": str(json_path),
        "panels": len(manifest["panels"]),
        "panels_included": panels_included,
    }


def recent_freezes(runs_dir: Path, limit: int = 10) -> list[dict]:
    """The most recent freeze manifests (parsed), newest first; corrupt files are skipped."""
    def key(path: Path) -> tuple[str, str, int]:
        match = re.fullmatch(r"freeze-(?:agent-)?(\d{6})(?:-(\d+))?", path.stem)
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
                 presence: ViewerPresence | None = None,
                 dumper: TreeDumper | None = None, driver: CuaDriver | None = None) -> None:
        super().__init__(address, ViewerHandler)
        self.adb = adb
        self.manager = manager
        self.device = device or {}
        self.runs_dir = Path(runs_dir)
        self.presence = presence
        self.dumper = dumper
        self.driver = driver
        self.trees: dict[int, dict] = {}

    def service_actions(self) -> None:
        super().service_actions()
        if self.presence is not None:
            self.presence.beat()


class ViewerHandler(ResponseMixin, BaseHTTPRequestHandler):
    server_version = "phone-lab/0.1"
    server: ViewerServer

    def log_message(self, fmt: str, *args) -> None:  # one line per request, never a serial
        print(self.server.adb.redact(f"{time.strftime('%H:%M:%S')} {fmt % args}"), flush=True)

    def _json(self, payload, status: int = 200, extra: dict[str, str] | None = None) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json", extra=extra)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, UI_PATH.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/state":
            state = self.server.manager.state() if self.server.manager else {}
            viewers = self.server.presence.others() if self.server.presence else []
            dumper = self.server.dumper
            tree_info = {
                "available": dumper is not None,
                "hello": getattr(dumper, "hello", None) if dumper else None,
                "restarts": getattr(dumper, "restarts", 0) if dumper else 0,
                "last_error": getattr(dumper, "last_error", None) if dumper else None,
            }
            self._json({"device": self.server.device, "server_time": time.time(),
                        "runs_dir": str(self.server.runs_dir), "viewers": viewers, "tree": tree_info, **state})
        elif path == "/api/freezes":
            self._json(recent_freezes(self.server.runs_dir))
        elif path.startswith("/api/tree/"):
            match = re.fullmatch(r"/api/tree/(\d+)", path)
            if not match:
                self._json({"error": "not found"}, 404)
                return
            logical_id = int(match.group(1))
            if self.server.dumper is None:
                self._json({"error": "treedump not configured"}, 503)
                return
            try:
                reply = self.server.dumper.tree(logical_id)
            except TreeError as exc:
                self._json({"error": self.server.adb.redact(str(exc))}, 503)
                return
            if not reply.get("ok"):
                self._json({"ok": False, "error": self.server.adb.redact(str(reply.get("error") or "display has no windows"))}, 404)
                return
            assign_refs(reply)
            displays = self.server.manager.state().get("displays", [])
            reply["display"] = next((d for d in displays if d.get("logical_id") == logical_id), None)
            self.server.trees[logical_id] = reply
            extra = {}
            if "cost_ms" in reply:
                extra["X-Tree-Cost-Ms"] = str(reply["cost_ms"])
            self._json(reply, 200, extra=extra)
        elif path.startswith("/frame/") and path.endswith(".jpg"):
            frame = self.server.manager.frame(unquote(path[len("/frame/"):-len(".jpg")])) if self.server.manager else None
            if frame is None:
                self._json({"error": "no frame yet"}, 404)
            else:
                self._send(200, frame.jpeg, "image/jpeg", {"X-Frame-Seq": str(frame.seq)})
        elif trace.handle_get(self, self.server.runs_dir, path):
            pass
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        split = urlsplit(self.path)
        path = split.path
        if path == "/api/freeze":
            qs = parse_qs(split.query)
            panels = qs.get("panels", ["all"])[0]
            if panels not in ("agent", "all"):
                self._json({"error": "panels must be agent or all"}, 400)
                return
            try:
                self._json(freeze(self.server.manager, self.server.device, self.server.runs_dir, panels_included=panels))
            except Exception as exc:  # report, never crash the server thread
                self._json({"error": self.server.adb.redact(f"freeze failed: {exc}")}, 500)
        elif path == "/api/tap":
            t0 = time.time()
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""
            try:
                data = json.loads(body) if body else {}
            except Exception:
                self._json({"ok": False, "reason": "invalid json"}, 400)
                return

            logical_id = data.get("logical_id")
            ref = data.get("ref")
            if logical_id is None:
                self._json({"ok": False, "reason": "missing logical_id"}, 400)
                return
            try:
                logical_id = int(logical_id)
            except (ValueError, TypeError):
                self._json({"ok": False, "reason": "invalid logical_id"}, 400)
                return

            displays = self.server.manager.state().get("displays", [])
            matching = next((d for d in displays if d.get("logical_id") == logical_id), None)
            if matching is None or matching.get("role") != "agent":
                self._json({"ok": False, "reason": "not an agent display"}, 400)
                return

            session = self.server.manager.registry.by_display().get(logical_id)
            if session is None or session.state != "active" or not session.target_id:
                self._json({"ok": False, "reason": f"no phone-lab session on display {logical_id}"}, 400)
                return

            if self.server.driver is None:
                self._json({"ok": False, "reason": "no cua-driver"}, 400)
                return

            if self.server.dumper is None:
                self._json({"ok": False, "reason": "no treedumper configured"}, 503)
                return

            try:
                tree = self.server.dumper.tree(logical_id)
            except TreeError as exc:
                self._json({"ok": False, "reason": self.server.adb.redact(str(exc))}, 503)
                return

            if not tree.get("ok"):
                self._json({"ok": False, "reason": "ref not found"}, 400)
                return

            assign_refs(tree)
            self.server.trees[logical_id] = tree
            node = find(tree, ref) if ref else None
            if node is None:
                self._json({"ok": False, "reason": "ref not found"}, 400)
                return

            x, y = tap_point(node)
            sid = session.session_id
            target = session.target_id
            stale_retries = 0
            snapshot_id = None
            frame_age_ms = None
            result = None

            while True:
                try:
                    snap = self.server.driver.snapshot(sid, target)
                    snap_data = snap.get("data", {})
                    snapshot_id = snap_data.get("snapshot_id")
                    frame_age_ms = snap_data.get("frame_age_ms")
                    self.server.driver.tap(sid, snapshot_id, x, y)
                    result = "ok"
                    break
                except CuaError as exc:
                    if exc.reason in ("frame_stale", "stale_snapshot") and stale_retries < 3:
                        stale_retries += 1
                        continue
                    result = f"refused:{exc.reason}" if exc.status == "refused" else f"error:{exc.reason}"
                    break

            now = time.time()
            session.last_action = {
                "kind": "tap ref",
                "at": now,
                "result": result,
                "detail": {
                    "ref": ref,
                    "x": x,
                    "y": y,
                    "frame_age_ms": frame_age_ms,
                    "stale_retries": stale_retries,
                },
            }
            session.updated_at = now
            self.server.manager.registry.write(session)

            if result != "ok":
                self._json({"ok": False, "reason": result}, 502)
                return

            cost_ms = round((time.time() - t0) * 1000)
            tree_cost_ms = tree.get("cost_ms", 0)
            self._json({
                "ok": True,
                "logical_id": logical_id,
                "ref": ref,
                "x": x,
                "y": y,
                "session_id": sid,
                "snapshot_id": snapshot_id,
                "frame_age_ms": frame_age_ms,
                "stale_retries": stale_retries,
                "tree_cost_ms": tree_cost_ms,
                "cost_ms": cost_ms,
            })
        elif path.startswith("/api/tree/") and path.endswith("/act"):
            match = re.fullmatch(r"/api/tree/(\d+)/act", path)
            if not match:
                self._json({"error": "not found"}, 404)
                return
            logical_id = int(match.group(1))

            displays = self.server.manager.state().get("displays", [])
            matching = next((d for d in displays if d.get("logical_id") == logical_id), None)
            if matching is None or matching.get("role") != "agent":
                self._json({"ok": False, "error": "not an agent display"}, 400)
                return

            if self.server.dumper is None:
                self._json({"ok": False, "error": "treedump not configured"}, 503)
                return

            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b""
            try:
                data = json.loads(body) if body else {}
            except Exception:
                self._json({"ok": False, "error": "invalid json"}, 400)
                return

            action = data.get("action")
            if action == "toast":
                text = data.get("text", "")
                try:
                    reply = dict(self.server.dumper.toast(logical_id, text))
                except TreeError as exc:
                    self._json({"ok": False, "error": self.server.adb.redact(str(exc))}, 503)
                    return
                reply["logical_id"] = logical_id
                self._json(reply)
            elif action in ("focus", "click"):
                ref = data.get("ref")
                if not ref:
                    self._json({"ok": False, "error": "missing ref"}, 400)
                    return
                tree = self.server.trees.get(logical_id)
                if not tree:
                    self._json({"ok": False, "error": f"no tree cached for display {logical_id}"}, 400)
                    return
                node = find(tree, ref)
                if not node:
                    self._json({"ok": False, "error": f"ref not found: {ref}"}, 400)
                    return
                node_index = node.get("i")
                if node_index is None:
                    self._json({"ok": False, "error": "node has no index"}, 400)
                    return
                try:
                    reply = dict(self.server.dumper.act(logical_id, node_index, action))
                except TreeError as exc:
                    self._json({"ok": False, "error": self.server.adb.redact(str(exc))}, 503)
                    return
                reply["logical_id"] = logical_id
                self._json(reply)
            else:
                self._json({"ok": False, "error": f"unknown action: {action}"}, 400)
        else:
            self._json({"error": "not found"}, 404)


def serve(adb: Adb, registry: Registry, host: str = "127.0.0.1", port: int = 8791,
          runs_dir: Path = Path("runs/phone-lab-runs"), max_height: int = 1000,
          driver: Path | None = None, treedump_jar: Path | None = None,
          stream_human: bool = False, stream_bitrate: int = 4_000_000,
          stream_max_fps: float = 5.0, apps: list[str] | tuple[str, ...] | None = None) -> int:
    """Run the viewer until Ctrl-C; capture threads start immediately."""
    if stream_human and shutil.which("ffmpeg") is None:
        print("error: --stream-human needs ffmpeg on PATH", file=sys.stderr)
        return 2

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

        source_factory = None
        if stream_human:
            from .stream import H264StreamSource

            def source_factory(d: Display) -> H264StreamSource | None:
                if d.role == "human" and d.logical_id == 0 and d.state == "ON":
                    return H264StreamSource(adb, d, max_height, stream_bitrate, stream_max_fps)
                return None

        manager = CaptureManager(adb, registry, max_height=max_height, source_factory=source_factory)
        server.manager = manager
        manager.start()

        if treedump_jar is not None:
            try:
                pkgs = packages_with(apps)
                dumper = TreeDumper(adb, Path(treedump_jar), text_packages=pkgs, act_packages=pkgs)
                print(adb.redact(json.dumps(dumper.start())), flush=True)
                server.dumper = dumper
            except TreeError as exc:
                print(adb.redact(f"treedump start failed: {exc}"), flush=True)
        server.driver = CuaDriver(adb, Path(driver)) if driver is not None else None

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
        if server.dumper is not None:
            try:
                server.dumper.stop()
            except Exception:
                pass
        print("phone-lab viewer stopped", flush=True)
