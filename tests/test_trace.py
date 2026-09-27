"""Tests for slice 4: trace viewer index, loaders, safety, server, and generator."""
from __future__ import annotations

import hashlib
import http.client
import inspect
import json
import tempfile
import threading
import unittest
from pathlib import Path

from phonelab.trace import (
    RUN_SCHEMA,
    STEP_SCHEMA,
    TraceError,
    TraceServer,
    list_runs,
    load_run,
    load_step,
    safe_file,
    valid_run_id,
)
from tests.synth_run import make_run


class TraceUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_runs_skips_and_orders(self):
        # 1. list_runs skips sessions/, a 20260926/ freeze folder, an empty directory,
        #    and a run with a corrupt run.json; orders newest first.
        (self.runs_dir / "sessions").mkdir()
        (self.runs_dir / "sessions" / "abc.json").write_text("{}\n")

        (self.runs_dir / "20260926").mkdir()
        (self.runs_dir / "20260926" / "freeze-120000.json").write_text("{}\n")

        (self.runs_dir / "empty-dir").mkdir()

        corrupt = self.runs_dir / "corrupt-run"
        corrupt.mkdir()
        (corrupt / "run.json").write_text("{not valid json\n")

        older = make_run(self.runs_dir, "run-older", steps=3, created_at=1_790_000_000.0)
        newer = make_run(self.runs_dir, "run-newer", steps=3, created_at=1_790_000_100.0)

        runs = list_runs(self.runs_dir)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["run_id"], "run-newer")
        self.assertEqual(runs[1]["run_id"], "run-older")
        self.assertEqual(runs[0]["step_count"], 3)
        self.assertEqual(runs[0]["kind"], "replay")
        self.assertIsNotNone(runs[0]["device"])

    def test_load_run_and_load_step(self):
        # 2. load_run returns the schema and step_dirs; load_step returns the parsed step
        #    and pending for a step dir with no step.json.
        make_run(self.runs_dir, "run-test", steps=3)
        data = load_run(self.runs_dir, "run-test")
        self.assertEqual(data["schema"], RUN_SCHEMA)
        self.assertEqual(data["step_dirs"], ["000", "001", "002"])

        step1 = load_step(self.runs_dir, "run-test", 1)
        self.assertEqual(step1["schema"], STEP_SCHEMA)
        self.assertEqual(step1["index"], 1)

        # Create an incomplete step dir without step.json
        incomplete_dir = self.runs_dir / "run-test" / "steps" / "099"
        incomplete_dir.mkdir(parents=True, exist_ok=True)
        pending_step = load_step(self.runs_dir, "run-test", 99)
        self.assertEqual(pending_step["schema"], STEP_SCHEMA)
        self.assertEqual(pending_step["index"], 99)
        self.assertTrue(pending_step.get("pending"))

        with self.assertRaises(TraceError) as ctx:
            load_step(self.runs_dir, "run-test", 999)
        self.assertEqual(ctx.exception.status, 404)

    def test_safe_file_validation(self):
        # 3. safe_file rejects ../x.png, absolute paths, steps/000/x.txt, steps/000/%2e%2e,
        #    a run id with a slash, and accepts steps/000/before-logical-98.png resolving inside the run.
        make_run(self.runs_dir, "run-safe", steps=2)

        # Parent traversal
        with self.assertRaises(TraceError) as ctx:
            safe_file(self.runs_dir, "run-safe", "../x.png")
        self.assertEqual(ctx.exception.status, 400)

        # Absolute paths
        with self.assertRaises(TraceError) as ctx:
            safe_file(self.runs_dir, "run-safe", "/etc/passwd")
        self.assertEqual(ctx.exception.status, 400)

        # Invalid extension
        with self.assertRaises(TraceError) as ctx:
            safe_file(self.runs_dir, "run-safe", "steps/000/x.txt")
        self.assertEqual(ctx.exception.status, 400)

        # Encoded traversal / bad segment characters
        with self.assertRaises(TraceError) as ctx:
            safe_file(self.runs_dir, "run-safe", "steps/000/%2e%2e")
        self.assertEqual(ctx.exception.status, 400)

        # Bad run_id with slash
        with self.assertRaises(TraceError) as ctx:
            safe_file(self.runs_dir, "run/slash", "steps/000/before-logical-98.png")
        self.assertEqual(ctx.exception.status, 400)

        # Valid file
        valid_path = safe_file(self.runs_dir, "run-safe", "steps/000/before-logical-98.png")
        self.assertTrue(valid_path.is_file())
        self.assertTrue(valid_path.name.endswith(".png"))


class TraceServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runs_dir = Path(self.tmp.name)
        make_run(self.runs_dir, "synth-1", steps=4, seed=1, created_at=1_790_000_000.0)
        make_run(self.runs_dir, "synth-2", steps=4, seed=2, created_at=1_790_000_100.0)

        self.server = TraceServer(("127.0.0.1", 0), self.runs_dir)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def _get(self, path: str) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", path)
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        body = resp.read()
        conn.close()
        return resp.status, headers, body

    def test_http_routes(self):
        # 4. HTTP: /trace is 200 HTML; /api/runs lists both synthetic runs; /api/runs/<id> and /steps/2
        #    return the schemas; /steps/999 is 404; /runs/<id>/steps/000/before-logical-98.png is 200
        #    image/png with a PNG signature; /runs/<id>/../run.json is 400 or 404; /nope is 404;
        #    / on the trace-only server redirects to /trace.

        # /trace
        status, headers, body = self._get("/trace")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("content-type", ""))
        self.assertIn(b"phone-lab", body)
        self.assertEqual(headers.get("cache-control"), "no-store")

        # /api/runs
        status, headers, body = self._get("/api/runs")
        self.assertEqual(status, 200)
        runs = json.loads(body.decode())
        self.assertEqual(len(runs), 2)

        # /api/runs/<id>
        status, _, body = self._get("/api/runs/synth-1")
        self.assertEqual(status, 200)
        run_data = json.loads(body.decode())
        self.assertEqual(run_data["schema"], RUN_SCHEMA)

        # /api/runs/<id>/steps/2
        status, _, body = self._get("/api/runs/synth-1/steps/2")
        self.assertEqual(status, 200)
        step_data = json.loads(body.decode())
        self.assertEqual(step_data["schema"], STEP_SCHEMA)
        self.assertEqual(step_data["index"], 2)

        # /api/runs/<id>/steps/999
        status, _, _ = self._get("/api/runs/synth-1/steps/999")
        self.assertEqual(status, 404)

        # /runs/<id>/steps/000/before-logical-98.png
        status, headers, body = self._get("/runs/synth-1/steps/000/before-logical-98.png")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("content-type"), "image/png")
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))

        # /runs/<id>/../run.json
        status, _, _ = self._get("/runs/synth-1/../run.json")
        self.assertIn(status, (400, 404))

        # /nope
        status, _, _ = self._get("/nope")
        self.assertEqual(status, 404)

        # / redirects to /trace
        status, headers, _ = self._get("/")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("location"), "/trace")


class SynthRunRoundTripTests(unittest.TestCase):
    def test_generator_round_trip(self):
        # 5. Generator round-trip: the failing run has result.status == "fail", step fail_at is fail,
        #    later steps skipped; every png_sha256 in every manifest equals the sha256 of the named file;
        #    every image and tree file named exists; run.steps length equals the number of step dirs.
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            run_path = make_run(runs_dir, "failing-run", steps=5, fail_at=2, drift_px=12)

            run_data = json.loads((run_path / "run.json").read_text())
            self.assertEqual(run_data["result"]["status"], "fail")
            self.assertEqual(len(run_data["steps"]), 5)

            step_dirs = [p for p in (run_path / "steps").iterdir() if p.is_dir()]
            self.assertEqual(len(run_data["steps"]), len(step_dirs))

            for idx, s_summary in enumerate(run_data["steps"]):
                if idx < 2:
                    self.assertEqual(s_summary["status"], "ok")
                elif idx == 2:
                    self.assertEqual(s_summary["status"], "fail")
                else:
                    self.assertEqual(s_summary["status"], "skipped")

                step_file = run_path / "steps" / f"{idx:03d}" / "step.json"
                self.assertTrue(step_file.is_file())
                step_data = json.loads(step_file.read_text())

                # Check captures
                for phase in ("before", "after"):
                    panels = step_data.get("captures", {}).get(phase, [])
                    for panel in panels:
                        img_name = panel.get("image")
                        sha = panel.get("png_sha256")
                        if img_name is not None:
                            img_file = run_path / "steps" / f"{idx:03d}" / img_name
                            self.assertTrue(img_file.is_file(), f"missing image file {img_file}")
                            actual_sha = hashlib.sha256(img_file.read_bytes()).hexdigest()
                            self.assertEqual(actual_sha, sha)
                        else:
                            self.assertIsNone(sha)

                # Check trees
                for phase in ("before", "after"):
                    trees_map = step_data.get("trees", {}).get(phase, {})
                    for disp_key, tree_filename in trees_map.items():
                        tree_file = run_path / "steps" / f"{idx:03d}" / tree_filename
                        self.assertTrue(tree_file.is_file(), f"missing tree file {tree_file}")
                        t_data = json.loads(tree_file.read_text())
                        self.assertEqual(t_data["schema"], "phone-lab.tree.v1")


class ServerWiringTests(unittest.TestCase):
    def test_handle_get_wired_in_viewer_handler(self):
        # 6. handle_get mounted in the live ViewerHandler path: construct ViewerServer is out of
        #    scope (needs Adb); instead assert that phonelab.server.ViewerHandler.do_GET source
        #    references handle_get (a cheap guard that the hook is wired).
        from phonelab.server import ViewerHandler
        source = inspect.getsource(ViewerHandler.do_GET)
        self.assertIn("handle_get", source)


if __name__ == "__main__":
    unittest.main()
