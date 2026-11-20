# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for per-power-source profile memory.

The interesting behaviour is which asusd slot a mode is written to, and that a
failed write never turns a successful profile change into an error.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import power_memory  # noqa: E402


class FakeAsusd:
    def __init__(
        self,
        fail: bool = False,
        *,
        ac_profile: int = 0,
        battery_profile: int = 2,
        ac_switch: bool = True,
        battery_switch: bool = True,
    ) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail = fail
        self.ac_profile = ac_profile
        self.battery_profile = battery_profile
        self.ac_switch = ac_switch
        self.battery_switch = battery_switch

    def set_profile_on_ac(self, profile: int) -> None:
        self._record("ac", profile)

    def set_profile_on_battery(self, profile: int) -> None:
        self._record("battery", profile)

    def profile_on_ac(self) -> int:
        return self.ac_profile

    def profile_on_battery(self) -> int:
        return self.battery_profile

    def change_profile_on_ac(self) -> bool:
        return self.ac_switch

    def change_profile_on_battery(self) -> bool:
        return self.battery_switch

    def _record(self, slot: str, profile: int) -> None:
        if self.fail:
            raise RuntimeError("asusd refused the write")
        self.calls.append((slot, profile))


class RememberTests(unittest.TestCase):
    def test_ac_writes_only_the_ac_slot(self) -> None:
        asusd = FakeAsusd()
        self.assertEqual(power_memory.remember(asusd, 1, ac=True), "ac")
        self.assertEqual(asusd.calls, [("ac", 1)])

    def test_battery_writes_only_the_battery_slot(self) -> None:
        asusd = FakeAsusd()
        self.assertEqual(power_memory.remember(asusd, 2, ac=False), "battery")
        self.assertEqual(asusd.calls, [("battery", 2)])

    def test_unknown_source_is_detected_from_sysfs(self) -> None:
        asusd = FakeAsusd()
        with mock.patch.object(power_memory, "ac_online", return_value=False):
            self.assertEqual(power_memory.remember(asusd, 0), "battery")
        self.assertEqual(asusd.calls, [("battery", 0)])

    def test_missing_power_supply_changes_nothing(self) -> None:
        asusd = FakeAsusd()
        with mock.patch.object(power_memory, "ac_online", return_value=None):
            self.assertIsNone(power_memory.remember(asusd, 0))
        self.assertEqual(asusd.calls, [])

    def test_failed_write_is_logged_not_raised(self) -> None:
        asusd = FakeAsusd(fail=True)
        with self.assertLogs("ghelper.backend.power_memory", level="WARNING") as logs:
            self.assertIsNone(power_memory.remember(asusd, 1, ac=True))
        self.assertIn("could not remember profile for ac", logs.output[0])

    def test_last_write_wins(self) -> None:
        asusd = FakeAsusd()
        power_memory.remember(asusd, 1, ac=True)
        power_memory.remember(asusd, 2, ac=True)
        self.assertEqual(asusd.calls, [("ac", 1), ("ac", 2)])


class TargetTests(unittest.TestCase):
    """What the overlay should announce after the cable moves."""

    def test_ac_uses_the_ac_memory(self) -> None:
        asusd = FakeAsusd(ac_profile=1, battery_profile=2)
        self.assertEqual(power_memory.target(asusd, True), 1)

    def test_battery_uses_the_battery_memory(self) -> None:
        asusd = FakeAsusd(ac_profile=1, battery_profile=2)
        self.assertEqual(power_memory.target(asusd, False), 2)

    def test_disabled_switching_returns_none(self) -> None:
        asusd = FakeAsusd(ac_switch=False, battery_switch=False)
        self.assertIsNone(power_memory.target(asusd, True))
        self.assertIsNone(power_memory.target(asusd, False))

    def test_balanced_is_zero_and_still_returned(self) -> None:
        # Balanced is profile 0; a falsy check would silently show the wrong
        # mode on every AC event for anyone who picked Balanced.
        asusd = FakeAsusd(ac_profile=0)
        self.assertEqual(power_memory.target(asusd, True), 0)

    def test_source_switches_are_checked_separately(self) -> None:
        asusd = FakeAsusd(ac_profile=1, battery_profile=2, battery_switch=False)
        self.assertEqual(power_memory.target(asusd, True), 1)
        self.assertIsNone(power_memory.target(asusd, False))


if __name__ == "__main__":
    unittest.main()
