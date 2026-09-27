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
        self.pidof_outputs: list[str] = []
        self.pidof_output: str = ""

    def redact(self, text: str) -> str:
        return text.replace(self.serial, "<serial>")

    def shell(self, *args: str, timeout: float = 15) -> str:
        self.shell_calls.append(args)
        if args == ("pidof", "screenrecord"):
            if self.pidof_outputs:
                return self.pidof_outputs.pop(0)
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
        d = Display(
            sf_id="1", unique_id="u1", name="Inner Display", kind="physical",
            logical_id=0, width=4, height=6, state="ON", owner=None,
            status_bar_px=0, role="human"
        )

        def make_fake_proc():
            return subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )

        # (a) with a resolvable pid the calls on stop are ("kill","-INT","200"),
        # ("pidof","screenrecord") and, when the pid is still listed, ("kill","-KILL","200"),
        # and NO pkill call appears; cleanup["device_pid"] == "200" and cleanup["broad"] is False
        adb_a = FakeStreamAdb()
        adb_a.pidof_outputs = ["100", "100 200", "100 200"]
        source_a = H264StreamSource(
            adb_a, d, max_height=6,
            spawn_recorder=lambda argv: make_fake_proc(),
            spawn_decoder=lambda argv: make_fake_proc(),
        )
        source_a.next_frame()
        source_a._resolve_device_pid()
        self.assertEqual(source_a.device_pid, "200")

        calls_before_stop = len(adb_a.shell_calls)
        source_a.stop()
        calls_on_stop = adb_a.shell_calls[calls_before_stop:]

        self.assertEqual(calls_on_stop, [
            ("kill", "-INT", "200"),
            ("pidof", "screenrecord"),
            ("kill", "-KILL", "200"),
        ])
        self.assertFalse(any("pkill" in c[0] for c in adb_a.shell_calls))
        self.assertEqual(source_a.cleanup["device_pid"], "200")
        self.assertFalse(source_a.cleanup["broad"])
        self.assertTrue(source_a.cleanup["killed"])
        self.assertEqual(source_a.cleanup["pids_after_int"], "100 200")

        adb_a2 = FakeStreamAdb()
        adb_a2.pidof_outputs = ["100", "100 200", "100"]
        source_a2 = H264StreamSource(
            adb_a2, d, max_height=6,
            spawn_recorder=lambda argv: make_fake_proc(),
            spawn_decoder=lambda argv: make_fake_proc(),
        )
        source_a2.next_frame()
        source_a2._resolve_device_pid()
        self.assertEqual(source_a2.device_pid, "200")

        calls_before_stop2 = len(adb_a2.shell_calls)
        source_a2.stop()
        calls_on_stop2 = adb_a2.shell_calls[calls_before_stop2:]

        self.assertEqual(calls_on_stop2, [
            ("kill", "-INT", "200"),
            ("pidof", "screenrecord"),
        ])
        self.assertFalse(any("pkill" in c[0] for c in adb_a2.shell_calls))
        self.assertEqual(source_a2.cleanup["device_pid"], "200")
        self.assertFalse(source_a2.cleanup["broad"])
        self.assertFalse(source_a2.cleanup["killed"])
        self.assertEqual(source_a2.cleanup["pids_after_int"], "100")

        # (b) when pidof shows no new pid (returns "100" every time) the broad fallback
        # runs with cleanup["broad"] is True and "reason" set
        adb_b = FakeStreamAdb()
        adb_b.pidof_output = "100"
        source_b = H264StreamSource(
            adb_b, d, max_height=6,
            spawn_recorder=lambda argv: make_fake_proc(),
            spawn_decoder=lambda argv: make_fake_proc(),
        )
        source_b.next_frame()
        source_b._resolve_device_pid()
        self.assertIsNone(source_b.device_pid)

        source_b.stop()
        self.assertTrue(source_b.cleanup["broad"])
        self.assertEqual(source_b.cleanup.get("reason"), "device pid unknown")
        self.assertIn(("pkill", "-INT", "screenrecord"), adb_b.shell_calls)
        self.assertIn(("pkill", "-KILL", "screenrecord"), adb_b.shell_calls)

        # (c) when two new pids appear ("100 200 300") the pid stays None and the broad fallback runs
        adb_c = FakeStreamAdb()
        adb_c.pidof_outputs = ["100", "100 200 300"]
        adb_c.pidof_output = ""
        source_c = H264StreamSource(
            adb_c, d, max_height=6,
            spawn_recorder=lambda argv: make_fake_proc(),
            spawn_decoder=lambda argv: make_fake_proc(),
        )
        source_c.next_frame()
        source_c._resolve_device_pid()
        self.assertIsNone(source_c.device_pid)

        source_c.stop()
        self.assertTrue(source_c.cleanup["broad"])
        self.assertEqual(source_c.cleanup.get("reason"), "device pid unknown")
        self.assertIn(("pkill", "-INT", "screenrecord"), adb_c.shell_calls)

        # (d) a source that never started does no shell calls on stop and records skipped
        adb_d = FakeStreamAdb()
        source_d = H264StreamSource(
            adb_d, d, max_height=6,
            spawn_recorder=lambda argv: make_fake_proc(),
            spawn_decoder=lambda argv: make_fake_proc(),
        )
        source_d.stop()
        self.assertEqual(adb_d.shell_calls, [])
        self.assertEqual(source_d.cleanup, {
            "device_pid": None,
            "broad": False,
            "skipped": "never started",
        })

        # Pump thread resolves pid on first chunk
        adb_p = FakeStreamAdb()
        adb_p.pidof_outputs = ["100", "100 888", "100 888"]
        rec_script_p = "import sys; sys.stdout.buffer.write(b'MARKER'); sys.stdout.buffer.flush(); import time; time.sleep(5)"
        dec_script_p = "import time; time.sleep(5)"
        source_p = H264StreamSource(
            adb_p, d, max_height=6,
            spawn_recorder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", rec_script_p], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
            spawn_decoder=lambda argv: subprocess.Popen(
                [sys.executable, "-c", dec_script_p], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            ),
        )
        try:
            source_p.next_frame()
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if source_p.device_pid is not None:
                    break
                time.sleep(0.02)
            self.assertEqual(source_p.device_pid, "888")
        finally:
            source_p.stop()
        self.assertEqual(source_p.cleanup["device_pid"], "888")
        self.assertFalse(source_p.cleanup["broad"])

        # next_frame bounded retry pid resolution
        adb_n = FakeStreamAdb()
        adb_n.pidof_outputs = ["100", "100", "100 999"]
        source_n = H264StreamSource(
            adb_n, d, max_height=6,
            spawn_recorder=lambda argv: make_fake_proc(),
            spawn_decoder=lambda argv: make_fake_proc(),
        )
        try:
            source_n.next_frame()
            self.assertIsNone(source_n.device_pid)
            source_n.next_frame()
            self.assertIsNone(source_n.device_pid)
            source_n._recorder_spawned_at = time.time() - 0.6
            source_n.next_frame()
            self.assertIsNone(source_n.device_pid)
            self.assertEqual(source_n._resolve_attempts, 1)
            source_n.next_frame()
            self.assertEqual(source_n._resolve_attempts, 1)
            source_n._last_resolve_attempt_at = time.time() - 1.1
            source_n.next_frame()
            self.assertEqual(source_n.device_pid, "999")
            self.assertEqual(source_n._resolve_attempts, 2)
        finally:
            source_n.stop()

        # Restart segment cleanup: scoped kill on old pid, never broad
        adb_r = FakeStreamAdb()
        adb_r.pidof_outputs = ["100", "100 200", "100 200", "100", "100 500"]
        source_r = H264StreamSource(
            adb_r, d, max_height=6,
            spawn_recorder=lambda argv: make_fake_proc(),
            spawn_decoder=lambda argv: make_fake_proc(),
        )
        try:
            source_r.next_frame()
            source_r._resolve_device_pid()
            self.assertEqual(source_r.device_pid, "200")
            source_r._restart_segment()
            self.assertFalse(any("pkill" in c[0] for c in adb_r.shell_calls))
            self.assertIsNone(source_r.device_pid)
            source_r._resolve_device_pid()
            self.assertEqual(source_r.device_pid, "500")
        finally:
            source_r.stop()

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
