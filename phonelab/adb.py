"""Thin ADB wrapper: device selection on a shared machine, redaction, screencap."""
from __future__ import annotations

import os
import re
import subprocess

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EMULATOR_PREFIX = "emulator-"


class AdbError(Exception):
    """ADB failed. Messages never contain a device serial."""


def parse_devices(text: str) -> list[dict[str, str]]:
    """Parse `adb devices -l`; only authorized (`device`) rows count.

    Each row carries `serial`, `model` (underscores to spaces) and `kind`:
    `emulator` for the Android emulator's `emulator-<port>` serials, else `physical`.
    """
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
        row["kind"] = "emulator" if row["serial"].startswith(EMULATOR_PREFIX) else "physical"
        rows.append(row)
    return rows


def normalize_model(name: str) -> str:
    return name.replace("_", " ").strip().casefold()


def device_tag(model: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", model.casefold()).strip("-")
    return cleaned or "unknown"


def describe(devices: list[dict[str, str]]) -> str:
    """Models and kinds only; never a serial."""
    return ", ".join(f"{d['model']} ({d['kind']})" for d in devices) or "none"


class Adb:
    """One device. Selection order: explicit serial, `ANDROID_SERIAL`, then the single physical device.

    Other agents may share the machine with emulators and phones of their own, so emulators are never
    chosen implicitly (`allow_emulators=True` opts in) and `model` narrows the choice by name.
    """

    def __init__(self, serial: str | None = None, adb: str = "adb", model: str | None = None,
                 allow_emulators: bool = False) -> None:
        self.adb = adb
        self.serial = serial or os.environ.get("ANDROID_SERIAL") or None
        self.wanted_model = model
        self.allow_emulators = allow_emulators
        self.model = "unknown"
        self.tag = "unknown"
        self.kind: str | None = None

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
        """Pick the device, or verify the requested one. Error messages name models, never serials."""
        devices = parse_devices(self._run([self.adb, "devices", "-l"], 15).stdout)
        if self.serial:
            match = [d for d in devices if d["serial"] == self.serial]
            if not match:
                raise AdbError(f"the requested device is not attached or not authorized; seen: {describe(devices)}")
            chosen = match[0]
        else:
            pool = devices
            if self.wanted_model:
                pool = [d for d in pool if normalize_model(d["model"]) == normalize_model(self.wanted_model)]
            if not self.allow_emulators:
                pool = [d for d in pool if d["kind"] == "physical"]
            what = ("device" if self.allow_emulators else "physical device") + (
                f" with model {self.wanted_model!r}" if self.wanted_model else "")
            if not pool:
                hint = ""
                if not self.allow_emulators and any(d["kind"] == "emulator" for d in devices):
                    hint = " (emulators are ignored unless --allow-emulators or --serial is given)"
                raise AdbError(f"no authorized {what} attached; seen: {describe(devices)}{hint}")
            if len(pool) > 1:
                raise AdbError(f"{len(pool)} authorized {what}s attached: {describe(pool)}; "
                               "pass --serial, --model, or set ANDROID_SERIAL")
            chosen = pool[0]
        self.serial, self.model, self.kind = chosen["serial"], chosen["model"], chosen["kind"]
        self.tag = device_tag(self.model)
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
