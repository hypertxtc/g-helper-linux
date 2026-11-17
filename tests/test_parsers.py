# SPDX-License-Identifier: GPL-3.0-or-later

"""Parser tests that do not need asusd, cardwire or a display."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import asusd, cardwire, display  # noqa: E402


class ProfileMappingTests(unittest.TestCase):
    def test_fan_profile_ids(self) -> None:
        self.assertEqual(asusd.fan_profile_for(asusd.BALANCED), 0)
        self.assertEqual(asusd.fan_profile_for(asusd.PERFORMANCE), 1)
        self.assertEqual(asusd.fan_profile_for(asusd.QUIET), 2)
        self.assertEqual(asusd.fan_profile_for(asusd.LOW_POWER), 2)
        self.assertEqual(asusd.fan_profile_for(asusd.CUSTOM), 3)
        self.assertEqual(asusd.fan_profile_for(99), 99)


class CardwireParserTests(unittest.TestCase):
    def test_parse_state(self) -> None:
        state = cardwire.parse_state(
            "Current Mode: Hybrid\nAvailable Mode: integrated, hybrid, smart\n"
        )
        self.assertEqual(state["current"], "hybrid")
        self.assertEqual(state["available"], ["integrated", "hybrid", "smart"])

    def test_parse_state_empty(self) -> None:
        state = cardwire.parse_state("")
        self.assertIsNone(state["current"])
        self.assertEqual(state["available"], [])

    def test_parse_gpus(self) -> None:
        gpus = cardwire.parse_gpus(
            '{"0": {"id": 0, "name": "iGPU"}, "1": {"id": 1, "name": "dGPU"}}'
        )
        self.assertEqual([gpu["name"] for gpu in gpus], ["iGPU", "dGPU"])

    def test_parse_gpus_list(self) -> None:
        gpus = cardwire.parse_gpus('[{"id": 0}, "junk"]')
        self.assertEqual(gpus, [{"id": 0}])

    def test_parse_gpus_invalid(self) -> None:
        with self.assertRaises(cardwire.CardwireError):
            cardwire.parse_gpus("not json")


class DisplayParserTests(unittest.TestCase):
    SAMPLE = (
        "\x1b[01;32mOutput: \x1b[0;0m1 eDP-2 1234-abcd\n"
        "\t\x1b[01;32menabled\x1b[0;0m\n"
        "\t\x1b[01;32mconnected\x1b[0;0m\n"
        "\t\x1b[01;33mPanel\x1b[0;0m\n"
        "\t\x1b[01;34mModes: \x1b[0;0m 1:\x1b[01;32m2560x1600@120.00*\x1b[0;0m!"
        "  2:2560x1600@60.00  3:1920x1080@120.00\n"
        "\t\x1b[01;33mGeometry: \x1b[0;0m0,0 1463x915\n"
        "\t\x1b[01;33mScale: \x1b[0;0m1.75\n"
    )

    def test_parse_single_panel(self) -> None:
        outputs = display.parse_outputs(self.SAMPLE)
        self.assertEqual(len(outputs), 1)
        panel = outputs[0]
        self.assertEqual(panel["name"], "eDP-2")
        self.assertTrue(panel["enabled"])
        self.assertTrue(panel["panel"])
        self.assertEqual(panel["scale"], 1.75)
        self.assertEqual(panel["geometry"], (1463, 915))
        self.assertEqual(len(panel["modes"]), 3)

    def test_current_mode_and_rates(self) -> None:
        panel = display.parse_outputs(self.SAMPLE)[0]
        mode = display.current_mode(panel)
        self.assertIsNotNone(mode)
        assert mode is not None
        self.assertEqual((mode["width"], mode["height"]), (2560, 1600))
        self.assertEqual(mode["rate"], 120.0)
        self.assertTrue(mode["current"])
        self.assertTrue(mode["preferred"])
        self.assertEqual(display.refresh_rates(panel), [120.0, 60.0])
        self.assertEqual(display.refresh_rates(panel, panel["modes"][2]), [120.0])

    def test_parse_ignores_leading_noise(self) -> None:
        self.assertEqual(display.parse_outputs("noise\nmore noise"), [])


if __name__ == "__main__":
    unittest.main()
