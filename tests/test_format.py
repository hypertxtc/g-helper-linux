# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for shared UI text formatting (no toolkit required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.ui.format import battery_line, status_line, temperature  # noqa: E402


class FormatTests(unittest.TestCase):
    def test_temperature(self) -> None:
        self.assertEqual(temperature(57.4), "57\u00b0C")
        self.assertEqual(temperature(41), "41\u00b0C")
        self.assertIsNone(temperature(None))
        self.assertIsNone(temperature("warm"))

    def test_status_line(self) -> None:
        line = status_line("Balanced", 57, 41.6, True)
        self.assertEqual(line, "Balanced  \u00b7  CPU 57\u00b0C  \u00b7  GPU 42\u00b0C  \u00b7  AC")

    def test_status_line_skips_missing_sensors(self) -> None:
        self.assertEqual(status_line("Turbo", None, 44, False), "Turbo  \u00b7  GPU 44\u00b0C  \u00b7  Battery")
        self.assertEqual(status_line("Silent", None, None, None), "Silent")

    def test_battery_discharging(self) -> None:
        line = battery_line(
            {
                "capacity": 78,
                "status": "Discharging",
                "power_w": 12.34,
                "energy_wh": 48.2,
            }
        )
        self.assertEqual(line, "78%  \u00b7  12.3 W  \u00b7  3h 54m")

    def test_battery_charging(self) -> None:
        line = battery_line(
            {"capacity": 64, "status": "Charging", "power_w": -42.0, "energy_wh": 40}
        )
        self.assertEqual(line, "64%  \u00b7  charging  \u00b7  42 W")

    def test_battery_full_and_idle(self) -> None:
        self.assertEqual(battery_line({"capacity": 100, "status": "Full"}), "100%  \u00b7  full")
        self.assertEqual(battery_line({"capacity": 80, "status": "Not charging"}), "80%")

    def test_battery_skips_unusable_values(self) -> None:
        self.assertEqual(battery_line(None), "")
        self.assertEqual(battery_line({}), "")
        self.assertEqual(battery_line({"status": "Charging"}), "")
        # No draw or no charge left: percent only, no bogus runtime.
        self.assertEqual(
            battery_line({"capacity": 5, "status": "Discharging", "power_w": 0}),
            "5%",
        )
        self.assertEqual(
            battery_line(
                {"capacity": 5, "status": "Discharging", "power_w": 9, "energy_wh": 0}
            ),
            "5%  \u00b7  9 W",
        )

    def test_short_runtime(self) -> None:
        line = battery_line(
            {"capacity": 9, "status": "Discharging", "power_w": 10, "energy_wh": 5}
        )
        self.assertEqual(line, "9%  \u00b7  10 W  \u00b7  30m")

    def test_status_line_includes_battery(self) -> None:
        line = status_line(
            "Balanced",
            57,
            41,
            False,
            {"capacity": 78, "status": "Discharging", "power_w": 12.3, "energy_wh": 48.2},
        )
        self.assertTrue(line.startswith("Balanced  \u00b7  CPU 57\u00b0C"))
        self.assertTrue(line.endswith("78%  \u00b7  12.3 W  \u00b7  3h 55m"))


if __name__ == "__main__":
    unittest.main()
