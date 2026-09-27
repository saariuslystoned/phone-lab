"""Server tests for tree and tap endpoints (no device)."""
from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from phonelab.adb import Adb
from phonelab.cua import CuaError
from phonelab.server import ViewerServer
from phonelab.sessions import Registry, SessionRecord

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "tree_cua_fixture.json"


class FakeCaptureManager:
    def __init__(self, registry: Registry, displays: list[dict] | None = None) -> None:
        self.registry = registry
        self._displays = displays or [
            {
                "logical_id": 0,
                "sf_id": "1",
                "unique_id": "local:1",
                "name": "Inner Display",
                "role": "human",
                "kind": "physical",
                "state": "ON",
                "width": 1080,
                "height": 1920,
            },
            {
                "logical_id": 101,
                "sf_id": "2",
                "unique_id": "virtual:2",
                "name": "Cua agent",
                "role": "agent",
                "kind": "virtual",
                "state": "ON",
                "width": 1080,
                "height": 1920,
            },
        ]

    def state(self) -> dict:
        return {
            "displays": self._displays,
            "inventory_error": None,
            "inventories": 1,
        }

    def frame(self, key: str):
        return None

    def frames(self):
        return []

    def sessions_now(self, now: float | None = None) -> dict[int, dict]:
        return {
            lid: {**rec.to_json(), "lease_remaining_now_ms": rec.lease_remaining_now(now)}
            for lid, rec in self.registry.by_display().items()
        }


class FakeDumper:
    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture
        self.calls: list[tuple] = []
        self.hello = {
            "ok": True,
            "hello": "phone-lab treedump 1",
            "pid": 12345,
            "connect_flags": 1,
            "suppresses_services": False,
            "text_packages": ["ai.cua.fixture.notes", "ai.cua.android.demo"],
        }
        self.restarts = 0
        self.last_error = None

    def tree(self, logical_id: int) -> dict:
        self.calls.append(("tree", logical_id))
        if logical_id == 101:
            return copy.deepcopy(self.fixture)
        if logical_id == 7:
            return {
                "ok": False,
                "error": "no windows on display 7",
                "display_id": 7,
                "windows": [],
                "nodes": [],
            }
        return {
            "ok": False,
            "error": f"no windows on display {logical_id}",
            "display_id": logical_id,
            "windows": [],
            "nodes": [],
        }

    def act(self, logical_id: int, node_index: int, action: str) -> dict:
        self.calls.append(("act", logical_id, node_index, action))
        return {"ok": True, "performed": True}

    def toast(self, logical_id: int, text: str) -> dict:
        self.calls.append(("toast", logical_id, text))
        return {"ok": True, "method": "enqueueTextToast"}


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.tap_errors: list[CuaError] = []

    def snapshot(self, sid: str, target: str) -> dict:
        self.calls.append(("snapshot", sid, target))
        return {"data": {"snapshot_id": "snap1", "frame_age_ms": 100}}

    def tap(self, sid: str, snapshot_id: str, x: int, y: int) -> dict:
        self.calls.append(("tap", sid, snapshot_id, x, y))
        if self.tap_errors:
            raise self.tap_errors.pop(0)
        return {"status": "ok", "data": {}}


def _request_json(url: str, method: str = "GET", data: dict | None = None) -> tuple[int, dict, dict]:
    req = urllib.request.Request(url, method=method)
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req.add_header("Content-Type", "application/json")
        req.data = body
    try:
        with urllib.request.urlopen(req) as resp:
            headers = dict(resp.headers)
            body = resp.read()
            return resp.status, json.loads(body.decode("utf-8")), headers
    except urllib.error.HTTPError as exc:
        headers = dict(exc.headers)
        body = exc.read()
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {"raw": body.decode("utf-8")}
        return exc.code, payload, headers


class ServerTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.runs_dir = Path(self.tmp.name)
        self.registry = Registry(self.runs_dir)
        self.manager = FakeCaptureManager(self.registry)
        self.adb = Adb(serial="FAKESERIAL")
        self.device = {"model": "Pixel 10 Pro Fold", "android_release": "17"}
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            self.fixture = json.load(f)
        self.dumper = FakeDumper(self.fixture)
        self.driver = FakeDriver()

        self.server = ViewerServer(
            ("127.0.0.1", 0),
            self.adb,
            self.manager,
            self.device,
            self.runs_dir,
            dumper=self.dumper,
            driver=self.driver,
        )
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join()
        self.tmp.cleanup()

    def test_tree_101_has_refs_and_display(self) -> None:
        status, data, headers = _request_json(f"http://127.0.0.1:{self.port}/api/tree/101")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("refs", data)
        self.assertGreater(data["refs"]["count"], 0)
        self.assertIn("display", data)
        self.assertIsNotNone(data["display"])
        self.assertEqual(data["display"]["logical_id"], 101)
        self.assertEqual(data["display"]["role"], "agent")
        self.assertEqual(headers.get("X-Tree-Cost-Ms"), "87")

        inc_node = next(n for n in data["nodes"] if n.get("id") == "ai.cua.fixture.notes:id/increment")
        self.assertIn("ref", inc_node)

    def test_tree_7_returns_404(self) -> None:
        status, data, _ = _request_json(f"http://127.0.0.1:{self.port}/api/tree/7")
        self.assertEqual(status, 404)
        self.assertFalse(data.get("ok", True))

    def test_tree_503_without_dumper(self) -> None:
        self.server.dumper = None
        status, data, _ = _request_json(f"http://127.0.0.1:{self.port}/api/tree/101")
        self.assertEqual(status, 503)
        self.assertIn("error", data)

    def test_tap_human_display_refused(self) -> None:
        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tap",
            method="POST",
            data={"logical_id": 0, "ref": "abcdef"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("reason"), "not an agent display")

    def test_tap_no_session_refused(self) -> None:
        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tap",
            method="POST",
            data={"logical_id": 101, "ref": "abcdef"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("reason"), "no phone-lab session on display 101")

    def test_tap_with_registry_session(self) -> None:
        now = time.time()
        record = SessionRecord(
            session_id="s1",
            label="test session",
            display_id=101,
            package="ai.cua.fixture.notes",
            target_id="t1",
            state="active",
            lease_remaining_ms=60000,
            lease_checked_at=now,
            last_action=None,
            owner="test_runner",
            updated_at=now,
        )
        self.registry.write(record)

        # First fetch tree to get the ref for the increment button
        _, tree_data, _ = _request_json(f"http://127.0.0.1:{self.port}/api/tree/101")
        inc_node = next(n for n in tree_data["nodes"] if n.get("id") == "ai.cua.fixture.notes:id/increment")
        inc_ref = inc_node["ref"]

        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tap",
            method="POST",
            data={"logical_id": 101, "ref": inc_ref},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("logical_id"), 101)
        self.assertEqual(data.get("ref"), inc_ref)
        self.assertEqual(data.get("x"), 540)
        self.assertEqual(data.get("y"), 263)

        self.assertEqual(self.driver.calls, [("snapshot", "s1", "t1"), ("tap", "s1", "snap1", 540, 263)])

        saved = self.registry.by_display()[101]
        self.assertIsNotNone(saved.last_action)
        self.assertEqual(saved.last_action["kind"], "tap ref")
        self.assertEqual(saved.last_action["result"], "ok")
        self.assertEqual(saved.last_action["detail"]["ref"], inc_ref)
        self.assertEqual(saved.last_action["detail"]["x"], 540)
        self.assertEqual(saved.last_action["detail"]["y"], 263)
        self.assertEqual(saved.last_action["detail"]["frame_age_ms"], 100)
        self.assertEqual(saved.last_action["detail"]["stale_retries"], 0)

    def test_tap_unknown_ref_refused(self) -> None:
        now = time.time()
        record = SessionRecord(
            session_id="s1",
            label="test session",
            display_id=101,
            package="ai.cua.fixture.notes",
            target_id="t1",
            state="active",
            lease_remaining_ms=60000,
            lease_checked_at=now,
            last_action=None,
            owner="test_runner",
            updated_at=now,
        )
        self.registry.write(record)

        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tap",
            method="POST",
            data={"logical_id": 101, "ref": "zzzzzz"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("reason"), "ref not found")

    def test_tap_stale_retry_and_refusal(self) -> None:
        now = time.time()
        record = SessionRecord(
            session_id="s1",
            label="test session",
            display_id=101,
            package="ai.cua.fixture.notes",
            target_id="t1",
            state="active",
            lease_remaining_ms=60000,
            lease_checked_at=now,
            last_action=None,
            owner="test_runner",
            updated_at=now,
        )
        self.registry.write(record)

        _, tree_data, _ = _request_json(f"http://127.0.0.1:{self.port}/api/tree/101")
        inc_node = next(n for n in tree_data["nodes"] if n.get("id") == "ai.cua.fixture.notes:id/increment")
        inc_ref = inc_node["ref"]

        # Retry once on frame_stale then succeed
        self.driver.tap_errors = [CuaError("refused", "frame_stale")]
        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tap",
            method="POST",
            data={"logical_id": 101, "ref": inc_ref},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("stale_retries"), 1)

        # Fatal refusal -> 502
        self.driver.tap_errors = [CuaError("refused", "device_locked")]
        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tap",
            method="POST",
            data={"logical_id": 101, "ref": inc_ref},
        )
        self.assertEqual(status, 502)
        self.assertFalse(data.get("ok"))
        self.assertEqual(data.get("reason"), "refused:device_locked")

    def test_act_focus_editor(self) -> None:
        # Pre-populate server.trees[101] by getting tree
        _, tree_data, _ = _request_json(f"http://127.0.0.1:{self.port}/api/tree/101")
        editor_node = next(n for n in tree_data["nodes"] if n.get("id") == "ai.cua.fixture.notes:id/editor")
        editor_ref = editor_node["ref"]
        editor_idx = editor_node["i"]

        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tree/101/act",
            method="POST",
            data={"action": "focus", "ref": editor_ref},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("logical_id"), 101)
        self.assertIn(("act", 101, editor_idx, "focus"), self.dumper.calls)

    def test_act_toast(self) -> None:
        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tree/101/act",
            method="POST",
            data={"action": "toast", "text": "hello toast"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("logical_id"), 101)
        self.assertIn(("toast", 101, "hello toast"), self.dumper.calls)

    def test_act_refused_on_human_display(self) -> None:
        status, data, _ = _request_json(
            f"http://127.0.0.1:{self.port}/api/tree/0/act",
            method="POST",
            data={"action": "toast", "text": "hello"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(data.get("ok"))

    def test_state_includes_tree(self) -> None:
        status, data, _ = _request_json(f"http://127.0.0.1:{self.port}/api/state")
        self.assertEqual(status, 200)
        self.assertIn("tree", data)
        tree_info = data["tree"]
        self.assertTrue(tree_info.get("available"))
        self.assertEqual(tree_info.get("hello", {}).get("hello"), "phone-lab treedump 1")
        self.assertEqual(tree_info.get("restarts"), 0)
        self.assertIsNone(tree_info.get("last_error"))


if __name__ == "__main__":
    unittest.main()
