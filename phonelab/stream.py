"""Per-display H.264 streaming frame source using adb screenrecord and ffmpeg."""
from __future__ import annotations

import io
import subprocess
import threading
import time
from typing import Any, Callable

from PIL import Image

from .adb import Adb, AdbError
from .capture import CaptureError, FrameSource, JPEG_QUALITY
from .displays import Display


class H264StreamSource(FrameSource):
    """H.264 stream frame source for a single display.

    Streams raw Annex-B H.264 video from `adb exec-out screenrecord` and decodes it via ffmpeg.
    Note that stream frames are downscaled (capped to max_height with aspect ratio preserved),
    unlike screencap frames which are full resolution.
    Cleanup is scoped to the recorder's own device pid.
    """

    label: str = "h264-stream"

    def __init__(self, adb: Adb, display: Display, max_height: int = 1000,
                 bitrate: int = 4_000_000, max_fps: float = 5.0, segment_s: int = 180,
                 ffmpeg: str = "ffmpeg",
                 spawn_recorder: Callable[[list[str]], Any] | None = None,
                 spawn_decoder: Callable[[list[str]], Any] | None = None) -> None:
        self.adb = adb
        self.display = display
        self.max_height = max_height
        self.bitrate = bitrate
        self.max_fps = max_fps
        self.segment_s = segment_s
        self.ffmpeg = ffmpeg
        self._spawn_recorder = spawn_recorder or (
            lambda argv: subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        )
        self._spawn_decoder = spawn_decoder or (
            lambda argv: subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        )

        self.out_w: int = 0
        self.out_h: int = 0
        self.last_frame_at: float | None = None
        self.cleanup: dict[str, Any] = {}

        self.device_pid: str | None = None
        self._pids_before_recorder: set[str] = set()
        self._recorder_ever_spawned: bool = False
        self._recorder_spawned_at: float | None = None
        self._resolve_attempts: int = 0
        self._last_resolve_attempt_at: float = 0.0
        self._pid_lock = threading.Lock()

        self._started: bool = False
        self._started_at: float | None = None
        self._halt = threading.Event()
        self._lock = threading.Lock()

        self.recorder: subprocess.Popen | None = None
        self.decoder: subprocess.Popen | None = None
        self._pump_thread: threading.Thread | None = None
        self._decode_thread: threading.Thread | None = None

        self._latest_raw: tuple[bytes, int] | None = None
        self._frame_counter: int = 0
        self._last_delivered_id: int = 0
        self._last_delivered_time: float = 0.0
        self._next_restart_time: float = 0.0

    @property
    def idle_s(self) -> float:
        """Seconds since the last decoded frame, or since start if no frame has arrived yet."""
        if self.last_frame_at is None:
            return (time.time() - self._started_at) if self._started_at is not None else 0.0
        return time.time() - self.last_frame_at

    def _compute_dimensions(self) -> tuple[int, int]:
        w, h = self.display.width, self.display.height
        if not w or not h:
            raise CaptureError(f"display {self.display.unique_id} has no width/height")
        out_h = min(self.max_height, h)
        out_w = int(round(w * out_h / h / 2.0)) * 2
        if out_w < 2:
            out_w = 2
        if out_h < 2:
            out_h = 2
        return out_w, out_h

    def _build_recorder_argv(self) -> list[str]:
        w, h = self.display.width, self.display.height
        if not w or not h:
            raise CaptureError(f"display {self.display.unique_id} has no width/height")
        argv = [self.adb.adb]
        if self.adb.serial:
            argv.extend(["-s", self.adb.serial])
        argv.extend([
            "exec-out",
            "screenrecord",
            "--output-format=h264",
            f"--size={w}x{h}",
            f"--bit-rate={self.bitrate}",
            f"--time-limit={self.segment_s}",
            "-",
        ])
        return argv

    def _build_decoder_argv(self) -> list[str]:
        return [
            self.ffmpeg,
            "-v", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "h264",
            "-i", "pipe:0",
            "-vf", f"scale={self.out_w}:{self.out_h}",
            "-pix_fmt", "rgb24",
            "-vsync", "passthrough",
            "-f", "rawvideo",
            "pipe:1",
        ]

    def _read_tail(self, pipe: Any) -> str:
        if pipe is None:
            return ""
        try:
            data = pipe.read()
            if isinstance(data, bytes):
                text = data.decode("utf-8", errors="replace")
            else:
                text = str(data)
            return text.strip()[-200:]
        except Exception:
            return ""

    def _cleanup_scoped(self, pid: str) -> None:
        try:
            self.adb.shell("kill", "-INT", pid, timeout=5)
        except AdbError:
            pass
        time.sleep(0.3)  # let screenrecord finish its SIGINT handler before checking
        pids = ""
        try:
            pids = self.adb.shell("pidof", "screenrecord", timeout=5).strip()
        except AdbError:
            pass
        killed = False
        if pid in pids.split():
            try:
                self.adb.shell("kill", "-KILL", pid, timeout=5)
                killed = True
            except AdbError:
                pass
        self.cleanup = {
            "device_pid": pid,
            "pids_after_int": pids,
            "killed": killed,
            "broad": False,
        }

    def _cleanup_processes(self, from_restart: bool = False) -> None:
        for proc in (self.recorder, self.decoder):
            if proc is not None:
                try:
                    proc.kill()
                except OSError:
                    pass
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                try:
                    proc.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if self._pump_thread is not None and self._pump_thread.is_alive():
            self._pump_thread.join(timeout=1.0)
        if self._decode_thread is not None and self._decode_thread.is_alive():
            self._decode_thread.join(timeout=1.0)
        self.recorder = None
        self.decoder = None

        if from_restart and self.device_pid is not None:
            self._cleanup_scoped(self.device_pid)
            self.device_pid = None

    def _resolve_device_pid(self) -> None:
        with self._pid_lock:
            self._last_resolve_attempt_at = time.time()
            if self.device_pid is not None:
                return
            try:
                out = self.adb.shell("pidof", "screenrecord", timeout=5)
                after = set(out.split())
                diff = after - self._pids_before_recorder
                if len(diff) == 1:
                    self.device_pid = next(iter(diff))
                else:
                    self.device_pid = None
            except AdbError:
                self.device_pid = None

    def _start(self) -> None:
        if self._halt.is_set():
            return
        now = time.time()
        if now < self._next_restart_time:
            wait_s = self._next_restart_time - now
            self._halt.wait(wait_s)
            if self._halt.is_set():
                return
        self.out_w, self.out_h = self._compute_dimensions()
        self._start_processes()
        self._started = True

    def _start_processes(self) -> None:
        rec_argv = self._build_recorder_argv()
        dec_argv = self._build_decoder_argv()

        try:
            before = set(self.adb.shell("pidof", "screenrecord", timeout=5).split())
        except AdbError:
            before = set()
        self._pids_before_recorder = before

        try:
            self.recorder = self._spawn_recorder(rec_argv)
        except FileNotFoundError as exc:
            raise CaptureError(self.adb.redact(f"could not start recorder: {exc}"))

        self._recorder_ever_spawned = True
        self._recorder_spawned_at = time.time()
        self.device_pid = None
        self._resolve_attempts = 0
        self._last_resolve_attempt_at = 0.0

        try:
            self.decoder = self._spawn_decoder(dec_argv)
        except FileNotFoundError:
            self._cleanup_processes()
            self._next_restart_time = time.time() + 1.0
            self._started = False
            raise CaptureError("ffmpeg not found on PATH; --stream-human needs it")

        if self._started_at is None:
            self._started_at = time.time()

        frame_bytes = self.out_w * self.out_h * 3
        self._pump_thread = threading.Thread(
            target=self._pump_loop,
            args=(self.recorder, self.decoder, self._halt),
            name=f"pump-{self.display.unique_id}",
            daemon=True,
        )
        self._pump_thread.start()

        self._decode_thread = threading.Thread(
            target=self._decode_loop,
            args=(self.decoder, frame_bytes, self._halt),
            name=f"decode-{self.display.unique_id}",
            daemon=True,
        )
        self._decode_thread.start()

    def _pump_loop(self, recorder: subprocess.Popen, decoder: subprocess.Popen, halt: threading.Event) -> None:
        reader = getattr(recorder.stdout, "read1", None)
        if not callable(reader):
            reader = recorder.stdout.read if recorder.stdout else None
        if reader is None:
            return
        first_chunk = True
        try:
            while not halt.is_set():
                chunk = reader(65536)
                if not chunk:
                    break
                if first_chunk:
                    first_chunk = False
                    self._resolve_device_pid()
                if decoder.stdin is not None:
                    decoder.stdin.write(chunk)
                    decoder.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            if decoder.stdin is not None:
                try:
                    decoder.stdin.close()
                except Exception:
                    pass

    def _decode_loop(self, decoder: subprocess.Popen, frame_bytes: int, halt: threading.Event) -> None:
        stdout = decoder.stdout
        if stdout is None:
            return
        while not halt.is_set():
            buf = bytearray()
            while len(buf) < frame_bytes and not halt.is_set():
                try:
                    chunk = stdout.read(frame_bytes - len(buf))
                except (ValueError, OSError):
                    return
                if not chunk:
                    return
                buf.extend(chunk)
            if halt.is_set() or len(buf) < frame_bytes:
                return
            now = time.time()
            with self._lock:
                self._frame_counter += 1
                self._latest_raw = (bytes(buf), self._frame_counter)
                self.last_frame_at = now

    def _restart_segment(self) -> None:
        if self._halt.is_set():
            return
        self._cleanup_processes(from_restart=True)
        self.device_pid = None
        self._start_processes()

    def _check_processes(self) -> None:
        if self.recorder is not None:
            rec_code = self.recorder.poll()
            if rec_code is not None:
                if rec_code == 0:
                    if self._pump_thread is None or not self._pump_thread.is_alive():
                        self._restart_segment()
                else:
                    stderr_tail = self._read_tail(self.recorder.stderr)
                    msg = (
                        f"recorder exited with code {rec_code}: {stderr_tail}".strip()
                        if stderr_tail else f"recorder exited with code {rec_code}"
                    )
                    msg = self.adb.redact(msg)
                    self._cleanup_processes()
                    self._next_restart_time = time.time() + 1.0
                    self._started = False
                    raise CaptureError(msg)

        if self.decoder is not None:
            dec_code = self.decoder.poll()
            if dec_code is not None:
                rec_running = self.recorder is not None and self.recorder.poll() is None
                if dec_code != 0 or rec_running:
                    stderr_tail = self._read_tail(self.decoder.stderr)
                    msg = (
                        f"decoder exited with code {dec_code}: {stderr_tail}".strip()
                        if stderr_tail else f"decoder exited with code {dec_code}"
                    )
                    msg = self.adb.redact(msg)
                    self._cleanup_processes()
                    self._next_restart_time = time.time() + 1.0
                    self._started = False
                    raise CaptureError(msg)

    def next_frame(self) -> tuple[bytes, bytes, int, int] | None:
        """Return the latest undelivered frame as (png, jpeg, width, height), or None.

        Frames are downscaled to out_w x out_h (unlike full-resolution screencap).
        """
        if self._halt.is_set():
            return None

        if not self._started:
            self._start()

        self._check_processes()

        now = time.time()
        if (
            self.device_pid is None
            and self.recorder is not None
            and self._recorder_spawned_at is not None
            and (now - self._recorder_spawned_at) >= 0.5
            and (now - self._last_resolve_attempt_at) >= 1.0
            and self._resolve_attempts < 3
        ):
            self._resolve_attempts += 1
            self._resolve_device_pid()

        with self._lock:
            if self._latest_raw is None:
                return None
            raw_bytes, frame_id = self._latest_raw
            if frame_id <= self._last_delivered_id:
                return None

            now = time.time()
            if self.max_fps > 0:
                min_interval = 1.0 / self.max_fps
                if (now - self._last_delivered_time) < min_interval:
                    return None

            self._last_delivered_id = frame_id
            self._last_delivered_time = now

        img = Image.frombytes("RGB", (self.out_w, self.out_h), raw_bytes)
        png_io = io.BytesIO()
        img.save(png_io, format="PNG")
        png_bytes = png_io.getvalue()

        jpeg_io = io.BytesIO()
        img.save(jpeg_io, format="JPEG", quality=JPEG_QUALITY)
        jpeg_bytes = jpeg_io.getvalue()

        return png_bytes, jpeg_bytes, self.out_w, self.out_h

    def stop(self) -> None:
        """Halt streaming, kill recorder/decoder processes, and clean up device-side screenrecord."""
        self._halt.set()
        for proc in (self.recorder, self.decoder):
            if proc is not None:
                try:
                    proc.kill()
                except OSError:
                    pass
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass
                try:
                    proc.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass

        deadline = time.time() + 3.0
        for thread in (self._pump_thread, self._decode_thread):
            if thread is not None and thread.is_alive():
                timeout = max(0.0, deadline - time.time())
                thread.join(timeout=timeout)
        self.recorder = None
        self.decoder = None

        if self.device_pid is not None:
            self._cleanup_scoped(self.device_pid)
        elif self._recorder_ever_spawned:
            try:
                self.adb.shell("pkill", "-INT", "screenrecord", timeout=5)
            except AdbError:
                pass
            pids = ""
            try:
                pids = self.adb.shell("pidof", "screenrecord", timeout=5).strip()
            except AdbError:
                pass
            killed = False
            if pids:
                try:
                    self.adb.shell("pkill", "-KILL", "screenrecord", timeout=5)
                    killed = True
                except AdbError:
                    pass
            self.cleanup = {
                "pids_after_int": pids,
                "killed": killed,
                "broad": True,
                "reason": "device pid unknown",
            }
        else:
            self.cleanup = {
                "device_pid": None,
                "broad": False,
                "skipped": "never started",
            }
