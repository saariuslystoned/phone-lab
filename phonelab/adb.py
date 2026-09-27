"""Thin ADB wrapper: single-device selection, redaction, screencap."""
from __future__ import annotations

import subprocess

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class AdbError(Exception):
    """ADB failed. Messages never contain a device serial."""


def parse_devices(text: str) -> list[dict[str, str]]:
    """Parse `adb devices -l`; only authorized (`device`) rows count."""
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        row = {"serial": parts[0]}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                row[key] = value
        row["model"] = row.get("model", "unknown").replace("_", " ")
        rows.append(row)
    return rows


class Adb:
    def __init__(self, serial: str | None = None, adb: str = "adb") -> None:
        self.adb = adb
        self.serial = serial
        self.model = "unknown"

    def redact(self, text: str) -> str:
        return text.replace(self.serial, "<serial>") if self.serial else text

    def _run(self, argv: list[str], timeout: float, binary: bool = False) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(argv, capture_output=True, text=not binary, timeout=timeout)
        except OSError as exc:
            raise AdbError(f"could not start {self.adb}: {self.redact(str(exc))}") from None
        except subprocess.TimeoutExpired:
            raise AdbError(f"adb {argv[-3:][0] if len(argv) > 2 else ''} timed out after {timeout}s") from None

    def _prefix(self) -> list[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else [])

    def resolve(self) -> "Adb":
        """Pick the single authorized device, or verify the requested one."""
        devices = parse_devices(self._run([self.adb, "devices", "-l"], 15).stdout)
        if self.serial:
            devices = [d for d in devices if d["serial"] == self.serial]
            if not devices:
                raise AdbError("the requested device is not attached or not authorized")
        elif not devices:
            raise AdbError("no authorized device attached")
        elif len(devices) > 1:
            raise AdbError(f"{len(devices)} authorized devices attached; pass --serial")
        self.serial, self.model = devices[0]["serial"], devices[0]["model"]
        return self

    def shell(self, *args: str, timeout: float = 15) -> str:
        result = self._run(self._prefix() + ["shell", *args], timeout)
        if result.returncode:
            raise AdbError(self.redact(f"adb shell {args[0]} failed: {result.stderr.strip()[:200]}"))
        return result.stdout

    def exec_out(self, *args: str, timeout: float = 30) -> bytes:
        return self._run(self._prefix() + ["exec-out", *args], timeout, binary=True).stdout

    def screencap(self, sf_display_id: str, timeout: float = 30) -> bytes | None:
        """PNG bytes of one SurfaceFlinger display, or None when the capture failed."""
        data = self.exec_out("screencap", "-p", "-d", sf_display_id, timeout=timeout)
        return data if data.startswith(PNG_SIGNATURE) else None

    def props(self) -> dict:
        release = self.shell("getprop", "ro.build.version.release").strip()
        sdk = self.shell("getprop", "ro.build.version.sdk").strip()
        return {"android_release": release, "api_level": int(sdk) if sdk.isdigit() else None}
