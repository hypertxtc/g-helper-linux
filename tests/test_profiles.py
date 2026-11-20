# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the shared profile table used by the buttons and the overlay."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import asusd as asusd_mod  # noqa: E402
from ghelper.ui import profiles  # noqa: E402


class ProfileTableTests(unittest.TestCase):
    def test_every_choice_has_a_name(self) -> None:
        for profile in asusd_mod.PROFILE_ORDER:
            with self.subTest(profile=profile):
                self.assertTrue(profiles.label(profile).strip())

    def test_low_power_is_called_silent(self) -> None:
        self.assertEqual(profiles.label(asusd_mod.QUIET), "Silent")
        self.assertEqual(profiles.label(asusd_mod.LOW_POWER), "Silent")

    def test_unknown_profile_falls_back_to_asusd_name(self) -> None:
        self.assertEqual(profiles.label(99), "99")

    def test_icon_names_drop_the_symbolic_suffix(self) -> None:
        self.assertEqual(profiles.icon_name(asusd_mod.BALANCED), "gh-balanced")
        self.assertEqual(profiles.icon_name(asusd_mod.BALANCED, dark=True), "gh-balanced-dark")

    def test_unknown_profile_gets_a_generic_icon(self) -> None:
        self.assertEqual(profiles.icon_name(99, dark=True), "gh-balanced-dark")


if __name__ == "__main__":
    unittest.main()
