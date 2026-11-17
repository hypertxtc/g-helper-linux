# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for hotkey device discovery, decoding and formatting."""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import hotkeys  # noqa: E402

SAMPLE = """I: Bus=0019 Vendor=0000 Product=0000 Version=0000
N: Name="Asus WMI hotkeys"
P: Phys=asus-nb-wmi/input0
H: Handlers=kbd event9 rfkill
B: EV=100013

I: Bus=0003 Vendor=0b05 Product=19b6 Version=0110
N: Name="ASUSTeK Computer Inc. N-KEY Device"
P: Phys=usb-0000:07:00.3-3/input2
H: Handlers=sysrq kbd leds event4

I: Bus=0003 Vendor=0b05 Product=19b6 Version=0110
N: Name="ASUSTeK Computer Inc. N-KEY Device"
P: Phys=usb-0000:07:00.3-3/input3
H: Handlers=mouse0 event5
"""


class DeviceParsingTests(unittest.TestCase):
    def test_parse_blocks(self) -> None:
        devices = hotkeys.parse_devices(SAMPLE)
        self.assertEqual(len(devices), 3)
        self.assertEqual(devices[0].name, "Asus WMI hotkeys")
        self.assertEqual(devices[0].path, "/dev/input/event9")
        self.assertEqual(devices[1].path, "/dev/input/event4")

    def test_filter_keeps_hotkey_devices_only(self) -> None:
        names = [d.name for d in hotkeys.parse_devices(SAMPLE) if hotkeys.is_hotkey_device(d)]
        self.assertEqual(
            names,
            ["Asus WMI hotkeys", "ASUSTeK Computer Inc. N-KEY Device"],
        )


class DecodeTests(unittest.TestCase):
    def test_decode_press(self) -> None:
        raw = struct.pack("llHHi", 12, 34, hotkeys.EV_KEY, 202, 1)
        self.assertEqual(hotkeys.decode_event(raw), (hotkeys.EV_KEY, 202, 1))

    def test_release_is_decoded(self) -> None:
        raw = struct.pack("llHHi", 0, 0, hotkeys.EV_KEY, 202, 0)
        self.assertEqual(hotkeys.decode_event(raw), (hotkeys.EV_KEY, 202, 0))


class NamingTests(unittest.TestCase):
    def test_known_codes(self) -> None:
        self.assertEqual(hotkeys.key_name(202), "KEY_PROG3")
        self.assertEqual(hotkeys.key_name(203), "KEY_PROG4")
        self.assertEqual(hotkeys.key_name(248), "KEY_MICMUTE")

    def test_labels_and_defaults(self) -> None:
        self.assertIn("ROG", hotkeys.key_label(202))
        self.assertEqual(hotkeys.DEFAULT_FN_ACTIONS, {"202": "toggle_window"})
        self.assertIn("toggle_window", hotkeys.ACTIONS)
        self.assertEqual(hotkeys.ACTION_ORDER[-1], "ignore")

    def test_unknown_code_is_hex(self) -> None:
        self.assertEqual(hotkeys.key_name(999), "KEY_0x3e7")


if __name__ == "__main__":
    unittest.main()
