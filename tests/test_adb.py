"""Device selection on a shared machine: emulators are never picked implicitly, models pick, serials never leak."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from phonelab.adb import Adb, AdbError, parse_devices

FOLD = "1A2B3C4D5E6F7G"
OTHER = "9Z8Y7X6W5V4U3T"
DEVICES = f"""List of devices attached
{FOLD}         device usb:0-1.4.2 product:rango model:Pixel_10_Pro_Fold device:rango transport_id:4
emulator-5554          device product:sdk_gphone64_arm64 model:sdk_gphone64_arm64 device:emu64a transport_id:8
"""
TWO_PHONES = DEVICES + f"{OTHER}         device usb:0-1.3 product:tokay model:Pixel_9 device:tokay transport_id:9\n"
UNAUTHORIZED = f"List of devices attached\n{FOLD}         unauthorized usb:0-1.4.2 transport_id:4\n"


def _adb(text: str, **kwargs) -> Adb:
    adb = Adb(**kwargs)
    adb._run = lambda argv, timeout, binary=False: SimpleNamespace(stdout=text, stderr="", returncode=0)
    return adb


class ParseTests(unittest.TestCase):
    def test_kinds_and_models(self):
        rows = parse_devices(TWO_PHONES)
        self.assertEqual([(r["model"], r["kind"]) for r in rows],
                         [("Pixel 10 Pro Fold", "physical"), ("sdk gphone64 arm64", "emulator"), ("Pixel 9", "physical")])

    def test_unauthorized_rows_are_skipped(self):
        self.assertEqual(parse_devices(UNAUTHORIZED), [])


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop("ANDROID_SERIAL", None)

    def tearDown(self):
        self.env.stop()

    def test_phone_wins_over_emulator(self):
        adb = _adb(DEVICES).resolve()
        self.assertEqual((adb.serial, adb.model, adb.kind), (FOLD, "Pixel 10 Pro Fold", "physical"))

    def test_emulator_alone_is_not_picked(self):
        only_emulator = "\n".join(l for l in DEVICES.splitlines() if FOLD not in l) + "\n"
        with self.assertRaises(AdbError) as ctx:
            _adb(only_emulator).resolve()
        self.assertIn("emulators are ignored", str(ctx.exception))
        adb = _adb(only_emulator, allow_emulators=True).resolve()
        self.assertEqual((adb.serial, adb.kind), ("emulator-5554", "emulator"))

    def test_two_phones_need_a_choice_and_message_names_models_only(self):
        with self.assertRaises(AdbError) as ctx:
            _adb(TWO_PHONES).resolve()
        message = str(ctx.exception)
        self.assertIn("2 authorized physical devices attached", message)
        self.assertIn("Pixel 9", message)
        self.assertNotIn(FOLD, message)
        self.assertNotIn(OTHER, message)

    def test_model_picks(self):
        adb = _adb(TWO_PHONES, model="pixel 9").resolve()
        self.assertEqual(adb.serial, OTHER)
        adb = _adb(TWO_PHONES, model="Pixel_10_Pro_Fold").resolve()
        self.assertEqual(adb.serial, FOLD)
        with self.assertRaises(AdbError) as ctx:
            _adb(TWO_PHONES, model="Pixel 8").resolve()
        self.assertIn("Pixel 8", str(ctx.exception))
        self.assertNotIn(OTHER, str(ctx.exception))

    def test_android_serial_env_is_honoured(self):
        os.environ["ANDROID_SERIAL"] = OTHER
        adb = _adb(TWO_PHONES).resolve()
        self.assertEqual(adb.model, "Pixel 9")
        self.assertEqual(adb.redact(f"x {OTHER} y"), "x <serial> y")

    def test_explicit_serial_can_pick_an_emulator(self):
        adb = _adb(DEVICES, serial="emulator-5554").resolve()
        self.assertEqual(adb.kind, "emulator")

    def test_missing_requested_serial(self):
        with self.assertRaises(AdbError) as ctx:
            _adb(DEVICES, serial="nope").resolve()
        self.assertNotIn(FOLD, str(ctx.exception))
        self.assertIn("Pixel 10 Pro Fold", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
