"""Unit tests for phonelab.stream.H264StreamSource."""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from phonelab.capture import CaptureError
from phonelab.displays import Display
from phonelab.server import serve
from phonelab.stream import H264StreamSource


class FakeStreamAdb:
    serial = "FAKESERIAL123"
    adb = "adb"

    def __init__(self) -> None:
        self.shell_calls: list[tuple[str, ...]] = []
        self.pidof_output: str = ""

    def redact(self, text: str) -> str:
        return text.replace(self.serial, "<serial>")

    def shell(self, *args: str, timeout: float = 15) -> str:
        self.shell_calls.append(args)
        if args == ("pidof", "screenrecord"):
            return self.pidof_output
        return ""


class StreamTests(unittest.TestCase):
    def test_frames_arrive_and_decode(self):
        adb = FakeStreamAdb()
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=4, height=6, state="ON", owner=None,
            status_bar_px=0, role="human"
        )
        rec_script = "import sys; sys.stdout.buffer.write(b'MARKER'); sys.stdout.buffer.flush()"
        dec_script = (
            "import sys, time; m = sys.stdin.buffer.read(6); "
            "frame = b'\\x10\\x20\\x30' * (4 * 6); "
            "sys.stdout.buffer.write(frame); sys.stdout.buffer.flush(); "
            "time.sleep(5)"
        )
        source = H264StreamSource(
            adb, d, max_height=6, max_fps=1000.0,
            spawn_recorder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", rec_script], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
            spawn_decoder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", dec_script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
        )
        try:
            res = None
            deadline = time.time() + 5.0
            while time.time() < deadline:
                res = source.next_frame()
                if res is not None:
                    break
                time.sleep(0.02)
            self.assertIsNotNone(res)
            png, jpeg, w, h = res
            self.assertEqual((w, h), (4, 6))
            img = Image.open(io.BytesIO(png))
            self.assertEqual(img.size, (4, 6))
            self.assertIsNotNone(source.last_frame_at)
            self.assertGreaterEqual(source.idle_s, 0.0)
        finally:
            source.stop()

    def test_rate_capping_drops_frames(self):
        adb = FakeStreamAdb()
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=4, height=6, state="ON", owner=None,
            status_bar_px=0, role="human"
        )
        rec_script = "import sys; sys.stdout.buffer.write(b'MARKER'); sys.stdout.buffer.flush()"
        dec_script = (
            "import sys, time; m = sys.stdin.buffer.read(6); "
            "frame1 = b'\\x10' * 72; frame2 = b'\\x20' * 72; "
            "sys.stdout.buffer.write(frame1); sys.stdout.buffer.flush(); "
            "time.sleep(0.05); "
            "sys.stdout.buffer.write(frame2); sys.stdout.buffer.flush(); "
            "time.sleep(5)"
        )
        source = H264StreamSource(
            adb, d, max_height=6, max_fps=0.5,
            spawn_recorder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", rec_script], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
            spawn_decoder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", dec_script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
        )
        try:
            f1 = None
            deadline = time.time() + 5.0
            while time.time() < deadline:
                f1 = source.next_frame()
                if f1 is not None:
                    break
                time.sleep(0.02)
            self.assertIsNotNone(f1)

            time.sleep(0.1)
            f2 = source.next_frame()
            self.assertIsNone(f2)
        finally:
            source.stop()

    def test_recorder_argv(self):
        adb = FakeStreamAdb()
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=1080, height=2400, state="ON", owner=None,
            status_bar_px=0, role="human"
        )
        captured_argv: list[str] = []

        def spawn_rec(argv: list[str]):
            captured_argv.extend(argv)
            return subprocess.Popen([sys.executable, "-c", ""], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        source = H264StreamSource(
            adb, d, max_height=1000, bitrate=3_000_000, segment_s=180,
            spawn_recorder=spawn_rec,
            spawn_decoder=lambda argv: subprocess.Popen([sys.executable, "-c", ""], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE),
        )
        try:
            source.next_frame()
        except Exception:
            pass
        finally:
            source.stop()

        self.assertIn("--output-format=h264", captured_argv)
        self.assertIn("--time-limit=180", captured_argv)
        self.assertIn("--size=1080x2400", captured_argv)
        self.assertIn("--bit-rate=3000000", captured_argv)
        self.assertIn("-s", captured_argv)
        self.assertIn("FAKESERIAL123", captured_argv)

    def test_recorder_nonzero_exit_redacts_serial(self):
        adb = FakeStreamAdb()
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=4, height=6, state="ON", owner=None,
            status_bar_px=0, role="human"
        )
        rec_script = "import sys; sys.stderr.write('failed on FAKESERIAL123'); sys.exit(1)"
        source = H264StreamSource(
            adb, d, max_height=6,
            spawn_recorder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", rec_script], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
            spawn_decoder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
        )
        try:
            deadline = time.time() + 5.0
            error_seen = None
            while time.time() < deadline:
                try:
                    source.next_frame()
                except CaptureError as exc:
                    error_seen = str(exc)
                    break
                time.sleep(0.02)
            self.assertIsNotNone(error_seen)
            self.assertIn("<serial>", error_seen)
            self.assertNotIn("FAKESERIAL123", error_seen)
        finally:
            source.stop()

    def test_stop_cleanup(self):
        adb = FakeStreamAdb()
        adb.pidof_output = ""
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=4, height=6, state="ON", owner=None,
            status_bar_px=0, role="human"
        )
        source = H264StreamSource(
            adb, d, max_height=6,
            spawn_recorder=lambda argv: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE),
            spawn_decoder=lambda argv: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE),
        )
        source.next_frame()
        source.stop()
        self.assertEqual(adb.shell_calls, [("pkill", "-INT", "screenrecord"), ("pidof", "screenrecord")])
        self.assertEqual(source.cleanup, {"pids_after_int": "", "killed": False})

        adb2 = FakeStreamAdb()
        adb2.pidof_output = "9876 5432"
        source2 = H264StreamSource(
            adb2, d, max_height=6,
            spawn_recorder=lambda argv: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE),
            spawn_decoder=lambda argv: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE),
        )
        source2.next_frame()
        source2.stop()
        self.assertEqual(adb2.shell_calls, [
            ("pkill", "-INT", "screenrecord"),
            ("pidof", "screenrecord"),
            ("pkill", "-KILL", "screenrecord"),
        ])
        self.assertEqual(source2.cleanup, {"pids_after_int": "9876 5432", "killed": True})

    def test_missing_ffmpeg_raises_capture_error(self):
        adb = FakeStreamAdb()
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=4, height=6, state="ON", owner=None,
            status_bar_px=0, role="human"
        )
        source = H264StreamSource(
            adb, d, ffmpeg="nonexistent_ffmpeg_binary_xyz_123",
            spawn_recorder=lambda argv: subprocess.Popen([sys.executable, "-c", ""], stdout=subprocess.PIPE, stderr=subprocess.PIPE),
        )
        try:
            with self.assertRaises(CaptureError) as ctx:
                source.next_frame()
            self.assertIn("ffmpeg not found on PATH; --stream-human needs it", str(ctx.exception))
        finally:
            source.stop()

    def test_missing_dimensions_raises_capture_error(self):
        adb = FakeStreamAdb()
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=None, height=None, state="ON", owner=None,
            status_bar_px=0, role="human"
        )
        source = H264StreamSource(adb, d)
        with self.assertRaises(CaptureError):
            source.next_frame()

    def test_serve_stream_human_missing_ffmpeg_returns_2(self):
        fake_adb = SimpleNamespace(
            model="Pixel 10 Pro Fold",
            tag="pixel-10-pro-fold",
            props=mock.Mock(return_value={}),
        )
        fake_registry = mock.Mock()
        with mock.patch("shutil.which", return_value=None):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = serve(fake_adb, fake_registry, stream_human=True)
            self.assertEqual(code, 2)
            self.assertIn("--stream-human needs ffmpeg", stderr.getvalue())

    def test_cli_parser_stream_human_flags(self):
        from phonelab.__main__ import build_parser
        parser = build_parser()
        args = parser.parse_args(["serve", "--stream-human", "--stream-bitrate", "2000000", "--stream-max-fps", "10.0"])
        self.assertTrue(args.stream_human)
        self.assertEqual(args.stream_bitrate, 2000000)
        self.assertEqual(args.stream_max_fps, 10.0)

        args_default = parser.parse_args(["serve"])
        self.assertFalse(args_default.stream_human)
        self.assertEqual(args_default.stream_bitrate, 4000000)
        self.assertEqual(args_default.stream_max_fps, 5.0)
