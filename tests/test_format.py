# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for shared UI text formatting (no toolkit required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.ui.format import status_line, temperature  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
