# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the opt-in automation daemon (no hardware access)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper import automation  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = automation.merge_config(None)
        self.assertFalse(config["gpu_mode"])
        self.assertFalse(config["refresh_rate"])
        self.assertEqual(automation.gpu_target(config, False), "integrated")
        self.assertEqual(automation.gpu_target(config, True), "hybrid")

    def test_overrides_and_clamping(self) -> None:
        config = automation.merge_config(
            {
                "gpu_on_battery": "smart",
                "gpu_on_ac": "integrated",
                "poll_seconds": 0,
                "refresh_on_ac": "60",
                "refresh_on_battery": 120,
            }
        )
        self.assertEqual(config["gpu_on_battery"], "smart")
        self.assertEqual(config["gpu_on_ac"], "integrated")
        self.assertEqual(config["poll_seconds"], 2)
        self.assertEqual(config["refresh_on_ac"], 60.0)
        self.assertEqual(config["refresh_on_battery"], 120)

    def test_invalid_values_fall_back(self) -> None:
        config = automation.merge_config(
            {"gpu_on_ac": "nonsense", "refresh_on_battery": "lots", "poll_seconds": "x"}
        )
        self.assertEqual(config["gpu_on_ac"], "hybrid")
        self.assertEqual(config["refresh_on_battery"], 60)
        self.assertEqual(config["poll_seconds"], 5)

    def test_refresh_target(self) -> None:
        config = automation.merge_config(None)
        self.assertEqual(automation.refresh_target(config, True, [120.0, 60.0]), 120.0)
        self.assertEqual(automation.refresh_target(config, False, [120.0, 60.0]), 60.0)
        self.assertIsNone(automation.refresh_target(config, True, []))

        config = automation.merge_config({"refresh_on_ac": 144})
        self.assertIsNone(automation.refresh_target(config, True, [120.0, 60.0]))


class ApplyTests(unittest.TestCase):
    def test_dry_run_battery_switch(self) -> None:
        config = automation.merge_config({"gpu_mode": True, "refresh_rate": False})
        with (
            mock.patch.object(automation.cardwire, "available", return_value=True),
            mock.patch.object(
                automation.cardwire, "get_state", return_value={"current": "hybrid"}
            ),
            mock.patch.object(automation.cardwire, "set_mode") as set_mode,
        ):
            actions = automation.Automation(config).apply(False, dry_run=True)
        self.assertIn("GPU mode: hybrid -> integrated", actions)
        set_mode.assert_not_called()

    def test_manual_cardwire_mode_is_respected(self) -> None:
        config = automation.merge_config({"gpu_mode": True, "refresh_rate": False})
        daemon = automation.Automation(config)
        with (
            mock.patch.object(automation.cardwire, "available", return_value=True),
            mock.patch.object(
                automation.cardwire, "get_state", return_value={"current": "smart"}
            ),
            mock.patch.object(automation.cardwire, "set_mode") as set_mode,
        ):
            actions = daemon.apply(False, dry_run=True)
            self.assertEqual(actions, [])
            daemon.apply(True, dry_run=True)
        set_mode.assert_not_called()

    def test_leaving_a_manual_mode_resumes_automation(self) -> None:
        config = automation.merge_config({"gpu_mode": True, "refresh_rate": False})
        daemon = automation.Automation(config)
        current = {"current": "smart"}
        with (
            mock.patch.object(automation.cardwire, "available", return_value=True),
            mock.patch.object(
                automation.cardwire, "get_state", side_effect=lambda: dict(current)
            ),
            mock.patch.object(automation.cardwire, "set_mode") as set_mode,
        ):
            self.assertEqual(daemon.apply(False, dry_run=True), [])
            current["current"] = "hybrid"
            actions = daemon.apply(False, dry_run=True)
        self.assertEqual(actions, ["GPU mode: hybrid -> integrated"])
        set_mode.assert_not_called()

    def test_no_change_when_already_on_target(self) -> None:
        config = automation.merge_config({"gpu_mode": True, "refresh_rate": False})
        with (
            mock.patch.object(automation.cardwire, "available", return_value=True),
            mock.patch.object(
                automation.cardwire, "get_state", return_value={"current": "hybrid"}
            ),
            mock.patch.object(automation.cardwire, "set_mode") as set_mode,
        ):
            actions = automation.Automation(config).apply(True, dry_run=True)
        self.assertEqual(actions, [])
        set_mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
