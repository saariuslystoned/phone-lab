"""TreeDumper: push the treedump jar, keep one device process, ask for trees."""
from __future__ import annotations

import hashlib
import json
import queue
import subprocess
import threading
import time
from pathlib import Path

from .adb import Adb

REMOTE_DIR = "/data/local/tmp/phonelab"
DEFAULT_TEXT_PACKAGES = ("ai.cua.fixture.notes", "ai.cua.android.demo")


class TreeError(Exception):
    """treedump error. Message is redacted."""


def parse_reply(line: str) -> dict:
    """json; raises TreeError on non-JSON or missing "ok"."""
    try:
        data = json.loads(line)
    except (ValueError, TypeError) as exc:
        raise TreeError(f"non-JSON reply: {exc}") from None
    if not isinstance(data, dict):
        raise TreeError("reply is not a JSON object")
    if "ok" not in data:
        raise TreeError("reply missing 'ok' field")
    return data


class TreeDumper:
    def __init__(
        self,
        adb: Adb,
        jar: Path,
        text_packages: tuple[str, ...] = DEFAULT_TEXT_PACKAGES,
        act_packages: tuple[str, ...] = DEFAULT_TEXT_PACKAGES,
        timeout: float = 10.0,
    ) -> None:
        self.adb = adb
        self.jar = Path(jar)
        self.text_packages = tuple(text_packages)
        self.act_packages = tuple(act_packages)
        self.timeout = timeout
        self.hello: dict | None = None
        self.restarts: int = 0
        self.last_error: str | None = None
        self._lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    def _log(self, message: str) -> None:
        print(self.adb.redact(f"{time.strftime('%H:%M:%S')} {message}"), flush=True)

    def push(self) -> dict:
        """mkdir -p REMOTE_DIR; push the jar only when `sha256sum` on the device differs; {"pushed": bool, "sha256": str}."""
        if not self.jar.is_file():
            err = self.adb.redact(f"jar file not found: {self.jar}")
            self.last_error = err
            raise TreeError(err)

        local_sha = hashlib.sha256(self.jar.read_bytes()).hexdigest()
        remote_jar = f"{REMOTE_DIR}/{self.jar.name}"

        try:
            self.adb.shell("mkdir", "-p", REMOTE_DIR, timeout=self.timeout)
        except Exception:
            pass

        remote_sha = None
        try:
            out = self.adb.shell("sha256sum", remote_jar, timeout=self.timeout)
            parts = out.strip().split()
            if parts:
                remote_sha = parts[0]
        except Exception:
            remote_sha = None

        pushed = False
        if remote_sha != local_sha:
            res = self.adb._run(self.adb._prefix() + ["push", str(self.jar), remote_jar], self.timeout)
            if res.returncode != 0:
                err = self.adb.redact(f"adb push failed: {res.stderr.strip()[:200]}")
                self.last_error = err
                raise TreeError(err)
            pushed = True

        return {"pushed": pushed, "sha256": local_sha}

    @staticmethod
    def _enqueue_output(pipe, q: queue.Queue[str | None]) -> None:
        try:
            for line in iter(pipe.readline, ""):
                q.put(line)
        except Exception:
            pass
        finally:
            q.put(None)

    def _start_process(self) -> dict:
        remote_jar = f"{REMOTE_DIR}/{self.jar.name}"
        cmd_parts = [
            f"CLASSPATH={remote_jar}",
            "app_process",
            REMOTE_DIR,
            "lab.phone.treedump.Main",
        ]
        if self.text_packages:
            cmd_parts.extend(["--text-packages", ",".join(self.text_packages)])
        if self.act_packages:
            cmd_parts.extend(["--act-packages", ",".join(self.act_packages)])
        shell_cmd = " ".join(cmd_parts)
        argv = self.adb._prefix() + ["shell", shell_cmd]

        try:
            self.proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # never read; a full pipe would stall the device process
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            err = self.adb.redact(f"could not start treedump: {exc}")
            self.last_error = err
            raise TreeError(err) from None

        self._queue = queue.Queue()
        self._reader_thread = threading.Thread(
            target=self._enqueue_output,
            args=(self.proc.stdout, self._queue),
            daemon=True,
        )
        self._reader_thread.start()

        hello = self._read_reply(timeout=self.timeout)
        if not hello.get("ok"):
            err = self.adb.redact(f"treedump failed to connect: {hello.get('error', 'unknown error')}")
            self.last_error = err
            raise TreeError(err)
        self.hello = hello
        return hello

    def start(self) -> dict:
        """push(), spawn `adb -s S shell CLASSPATH=… app_process … Main --text-packages …`, read the hello line; returns it."""
        with self._lock:
            self.push()
            return self._start_process()

    def _stop_process(self) -> None:
        if self.proc is None:
            return
        killed = False
        if self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write("quit\n")
                    self.proc.stdin.flush()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=2.0)
                except Exception:
                    pass
                killed = True
        if killed or (self.proc.poll() is None):
            try:
                self.adb.shell("pkill", "-f", "lab.phone.treedump", timeout=self.timeout)
            except Exception:
                pass
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if pipe:
                try:
                    pipe.close()
                except Exception:
                    pass
        self.proc = None

    def stop(self) -> None:
        """send quit, wait 2 s, kill the subprocess; `pkill -f lab.phone.treedump` on the device as a last resort."""
        with self._lock:
            self._stop_process()

    def _send_line(self, cmd: str) -> None:
        if self.proc is None or self.proc.poll() is not None:
            err = self.adb.redact("treedump process is not running")
            self.last_error = err
            raise TreeError(err)
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            err = self.adb.redact(f"failed to send command: {exc}")
            self.last_error = err
            raise TreeError(err) from None

    def _read_reply(self, timeout: float) -> dict:
        lines_skipped = 0
        while True:
            try:
                line = self._queue.get(timeout=timeout)
            except queue.Empty:
                err = self.adb.redact(f"timed out after {timeout}s waiting for reply")
                self.last_error = err
                raise TreeError(err) from None
            if line is None or line == "":
                err = self.adb.redact("unexpected EOF from treedump process")
                self.last_error = err
                raise TreeError(err)
            line = line.strip()
            if not line:
                continue
            try:
                reply = parse_reply(line)
                return reply
            except TreeError:
                lines_skipped += 1
                self._log(f"treedump noise line ({lines_skipped}/20): {line}")
                if lines_skipped >= 20:
                    err = self.adb.redact(f"too many non-JSON lines ({lines_skipped}): {line[:100]}")
                    self.last_error = err
                    raise TreeError(err)

    def displays(self) -> list[int]:
        with self._lock:
            self._send_line("displays")
            reply = self._read_reply(timeout=self.timeout)
            if not reply.get("ok"):
                err = self.adb.redact(f"displays failed: {reply.get('error', 'unknown error')}")
                self.last_error = err
                raise TreeError(err)
            return [int(x) for x in reply.get("display_ids", [])]

    def _tree_once(self, logical_id: int) -> dict:
        self._send_line(f"tree {logical_id}")
        reply = self._read_reply(timeout=self.timeout)
        if reply.get("ok") and not reply.get("windows") and not reply.get("nodes"):
            # Right after connecting, the accessibility window cache can be empty for a moment; ask once more.
            time.sleep(0.3)
            self._send_line(f"tree {logical_id}")
            reply = self._read_reply(timeout=self.timeout)
        return reply

    def tree(self, logical_id: int) -> dict:
        """one `tree` round-trip; restarts the process once on EOF/timeout, then raises TreeError."""
        with self._lock:
            try:
                return self._tree_once(logical_id)
            except TreeError as exc:
                self._log(f"tree failed ({exc}); restarting process")
                self.restarts += 1
                self._stop_process()
                self._start_process()
                return self._tree_once(logical_id)

    def act(self, logical_id: int, node_index: int, action: str) -> dict:
        with self._lock:
            self._send_line(f"act {logical_id} {node_index} {action}")
            return self._read_reply(timeout=self.timeout)

    def toast(self, logical_id: int, text: str) -> dict:
        with self._lock:
            self._send_line(f"toast {logical_id} {text}")
            return self._read_reply(timeout=self.timeout)
