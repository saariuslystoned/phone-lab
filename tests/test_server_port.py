"""Server port binding tests: ephemeral ports, port conflicts, fail-fast before adb."""
from __future__ import annotations

import contextlib
import io
import socket
import unittest
from types import SimpleNamespace
from unittest import mock

from phonelab.server import PortInUse, bind_viewer, serve, viewer_url


class ServerPortTests(unittest.TestCase):
    def test_bind_viewer_port_zero_and_viewer_url(self):
        server = bind_viewer(port=0)
        try:
            real_port = server.server_address[1]
            self.assertNotEqual(real_port, 0)
            url = viewer_url(server)
            self.assertIn(str(real_port), url)
            self.assertEqual(url, f"http://127.0.0.1:{real_port}/")
        finally:
            server.server_close()

    def test_busy_port_raises_port_in_use(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]
        try:
            with self.assertRaises(PortInUse) as ctx:
                server = bind_viewer(port=busy_port)
                server.server_close()
            msg = str(ctx.exception)
            self.assertIn(str(busy_port), msg)
            self.assertIn("--port 0", msg)
        finally:
            sock.close()

    def test_serve_on_busy_port_returns_2_without_calling_adb_props(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]
        fake_adb = SimpleNamespace(
            model="Pixel 10 Pro Fold",
            tag="pixel-10-pro-fold",
            props=mock.Mock(side_effect=AssertionError("adb.props() must not be called on busy port")),
        )
        fake_registry = SimpleNamespace()
        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                rc = serve(fake_adb, fake_registry, host="127.0.0.1", port=busy_port)
            self.assertEqual(rc, 2)
            self.assertIn(f"error: port {busy_port}", stderr.getvalue())
            fake_adb.props.assert_not_called()
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
