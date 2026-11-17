# SPDX-License-Identifier: GPL-3.0-or-later

"""GPU decision tests: the rules that broke before, pinned down.

These cover the two regressions from 0.2.0: asking cardwire for its auto-switch
state on the GTK thread (a multi-second freeze), and trusting the firmware
attribute instead of the PCI bus when deciding whether cardwire can switch.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghelper.backend import gpu_policy  # noqa: E402


class OptimizedTargetTests(unittest.TestCase):
    def test_ac_uses_hybrid(self) -> None:
        self.assertEqual(gpu_policy.optimized_target(True), "hybrid")

    def test_battery_uses_integrated(self) -> None:
        self.assertEqual(gpu_policy.optimized_target(False), "integrated")

    def test_unknown_power_prefers_integrated(self) -> None:
        self.assertEqual(gpu_policy.optimized_target(None), "integrated")


class CardwireCanSwitchTests(unittest.TestCase):
    def test_needs_firmware_off_and_a_present_dgpu(self) -> None:
        self.assertTrue(gpu_policy.cardwire_can_switch(False, True))

    def test_firmware_eco_blocks_cardwire(self) -> None:
        self.assertFalse(gpu_policy.cardwire_can_switch(True, True))

    def test_absent_dgpu_blocks_cardwire(self) -> None:
        self.assertFalse(gpu_policy.cardwire_can_switch(False, False))

    def test_unknown_bus_is_not_treated_as_absent(self) -> None:
        self.assertTrue(gpu_policy.cardwire_can_switch(False, None))


class OptimizedPlanTests(unittest.TestCase):
    def test_firmware_eco_skips_cardwire(self) -> None:
        plan = gpu_policy.optimized_plan(True, True, True)
        self.assertEqual(plan.action, gpu_policy.OPTIMIZED_FIRMWARE_ECO)
        self.assertFalse(plan.switched)
        self.assertIsNone(plan.target)

    def test_absent_dgpu_skips_cardwire(self) -> None:
        # DgpuDisable can read 0 while the card is still missing until rescan.
        plan = gpu_policy.optimized_plan(False, False, True)
        self.assertEqual(plan.action, gpu_policy.OPTIMIZED_DGPU_ABSENT)
        self.assertFalse(plan.switched)

    def test_healthy_state_switches(self) -> None:
        plan = gpu_policy.optimized_plan(False, True, True)
        self.assertEqual(plan.action, gpu_policy.OPTIMIZED_SWITCHED)
        self.assertTrue(plan.switched)
        self.assertEqual(plan.target, "hybrid")

    def test_unknown_bus_still_switches(self) -> None:
        plan = gpu_policy.optimized_plan(False, None, False)
        self.assertEqual(plan.target, "integrated")


class AutomationShouldSkipTests(unittest.TestCase):
    def _skip(self, **overrides: object) -> bool:
        state = {
            "gpu_auto": True,
            "cw_available": True,
            "cw_auto": False,
            "cw_current": "hybrid",
            "firmware_disabled": False,
            "dgpu_present": True,
        }
        state.update(overrides)
        return gpu_policy.automation_should_skip(**state)

    def test_all_clear_is_not_skipped(self) -> None:
        self.assertFalse(self._skip())

    def test_switching_off_optimized_stops_automation(self) -> None:
        self.assertTrue(self._skip(gpu_auto=False))

    def test_missing_cardwire_stops_automation(self) -> None:
        self.assertTrue(self._skip(cw_available=False))

    def test_firmware_eco_stops_automation(self) -> None:
        self.assertTrue(self._skip(firmware_disabled=True))

    def test_absent_dgpu_stops_automation(self) -> None:
        self.assertTrue(self._skip(dgpu_present=False))

    def test_unknown_auto_switch_defers(self) -> None:
        # Asking cardwire blocks, so an unknown answer must not mean "do it
        # ourselves" -- that is what froze the window for ten seconds.
        self.assertTrue(self._skip(cw_auto=None))

    def test_cardwire_handling_automation_defers(self) -> None:
        self.assertTrue(self._skip(cw_auto=True))

    def test_smart_mode_is_not_overridden(self) -> None:
        # Smart blocks the dGPU except for launched apps; moving to integrated
        # or hybrid on a power change would silently defeat it.
        self.assertTrue(self._skip(cw_current="smart"))

    def test_manual_mode_is_not_overridden(self) -> None:
        self.assertTrue(self._skip(cw_current="manual"))

    def test_integrated_mode_can_still_be_automated(self) -> None:
        self.assertFalse(self._skip(cw_current="integrated"))


class FirmwareQueueTests(unittest.TestCase):
    def test_only_standard_writes_firmware(self) -> None:
        self.assertTrue(gpu_policy.firmware_queue_needed("hybrid"))

    def test_eco_stays_a_runtime_mode(self) -> None:
        self.assertFalse(gpu_policy.firmware_queue_needed("integrated"))

    def test_ultimate_does_not_use_this_path(self) -> None:
        self.assertFalse(gpu_policy.firmware_queue_needed("ultimate"))


class ModeOutcomeTests(unittest.TestCase):
    def test_changed_wins(self) -> None:
        self.assertEqual(
            gpu_policy.mode_outcome(
                changed=True,
                firmware_disabled=False,
                dgpu_present=True,
                cw_mode="hybrid",
            ),
            gpu_policy.MODE_CHANGED,
        )

    def test_standard_on_absent_gpu_asks_for_a_reboot(self) -> None:
        self.assertEqual(
            gpu_policy.mode_outcome(
                changed=False,
                firmware_disabled=False,
                dgpu_present=False,
                cw_mode="hybrid",
            ),
            gpu_policy.MODE_REBOOT,
        )

    def test_eco_on_absent_gpu_is_not_a_reboot_prompt(self) -> None:
        self.assertEqual(
            gpu_policy.mode_outcome(
                changed=False,
                firmware_disabled=False,
                dgpu_present=False,
                cw_mode="integrated",
            ),
            gpu_policy.MODE_OK,
        )


class SelectionTests(unittest.TestCase):
    def _select(self, **overrides: object) -> gpu_policy.GpuSelection:
        state = {
            "gpu_auto": False,
            "current": "hybrid",
            "dgpu_value": 0,
            "dgpu_queued": -1,
            "mux_value": 1,
            "mux_queued": -1,
        }
        state.update(overrides)
        return gpu_policy.selection(**state)

    def test_optimized_is_selected_when_no_custom_mode_is_active(self) -> None:
        state = self._select(gpu_auto=True, current="hybrid")
        self.assertEqual(state.selected, "optimized")
        self.assertEqual(state.title, "GPU Mode: Optimized")

    def test_live_smart_beats_a_stale_optimized_flag(self) -> None:
        # Smart can be set from Settings or the CLI; report what cardwire is
        # actually doing instead of the stored intent.
        state = self._select(gpu_auto=True, current="smart")
        self.assertIsNone(state.selected)
        self.assertEqual(state.title, "GPU Mode: Smart")

    def test_smart_is_named_and_not_claimed_as_standard(self) -> None:
        state = self._select(current="smart")
        self.assertIsNone(state.selected)
        self.assertEqual(state.title, "GPU Mode: Smart")
        self.assertNotIn("iGPU + dGPU", state.title)

    def test_manual_is_named(self) -> None:
        state = self._select(current="manual")
        self.assertIsNone(state.selected)
        self.assertEqual(state.title, "GPU Mode: Manual")

    def test_integrated_is_eco(self) -> None:
        state = self._select(current="integrated")
        self.assertEqual(state.selected, "eco")
        self.assertEqual(state.title, "GPU Mode: iGPU only")

    def test_firmware_disable_is_eco(self) -> None:
        self.assertEqual(self._select(dgpu_value=1).selected, "eco")

    def test_queued_disable_is_eco(self) -> None:
        self.assertEqual(self._select(dgpu_queued=1).selected, "eco")

    def test_mux_is_ultimate(self) -> None:
        self.assertEqual(self._select(mux_value=0).selected, "ultimate")

    def test_queued_mux_is_ultimate(self) -> None:
        self.assertEqual(self._select(mux_queued=0).selected, "ultimate")

    def test_hybrid_is_standard(self) -> None:
        state = self._select()
        self.assertEqual(state.selected, "standard")
        self.assertEqual(state.title, "GPU Mode: iGPU + dGPU")

    def test_unknown_mode_is_standard(self) -> None:
        self.assertEqual(self._select(current=None).selected, "standard")


class TrayModeTests(unittest.TestCase):
    def test_live_smart_beats_the_stored_preset(self) -> None:
        self.assertEqual(gpu_policy.tray_mode("optimized", "smart"), "smart")

    def test_manual_has_no_tray_radio(self) -> None:
        self.assertIsNone(gpu_policy.tray_mode("standard", "manual"))

    def test_presets_map_to_their_radio(self) -> None:
        for mode in ("eco", "standard", "optimized"):
            with self.subTest(mode=mode):
                self.assertEqual(gpu_policy.tray_mode(mode, "hybrid"), mode)

    def test_ultimate_is_not_a_tray_mode(self) -> None:
        self.assertIsNone(gpu_policy.tray_mode("ultimate", "hybrid"))

    def test_unknown_state_checks_nothing(self) -> None:
        self.assertIsNone(gpu_policy.tray_mode(None, None))


class ErrorTranslationTests(unittest.TestCase):
    ENOENT = "IO Error: No such file or directory (os error 2)"

    def test_eco_message_mentions_the_firmware_switch(self) -> None:
        message = gpu_policy.cardwire_error_message(self.ENOENT, True, True)
        assert message is not None
        self.assertIn("ECO", message)
        self.assertIn("Standard", message)

    def test_absent_message_mentions_a_reboot(self) -> None:
        message = gpu_policy.cardwire_error_message(self.ENOENT, False, False)
        assert message is not None
        self.assertIn("reboot", message)

    def test_enoent_with_a_healthy_gpu_is_not_hidden(self) -> None:
        self.assertIsNone(gpu_policy.cardwire_error_message(self.ENOENT, False, True))

    def test_unrelated_errors_pass_through(self) -> None:
        self.assertIsNone(
            gpu_policy.cardwire_error_message("cardwire daemon is not running", False, True)
        )


class UiThreadGuardTests(unittest.TestCase):
    """cardwire's auto-switch query must stay on the worker thread."""

    def setUp(self) -> None:
        self.source = (ROOT / "src/ghelper/ui/main_view.py").read_text()

    def test_auto_switch_is_asked_exactly_once(self) -> None:
        calls = [
            line
            for line in self.source.splitlines()
            if "battery_auto_switch(" in line
        ]
        self.assertEqual(len(calls), 1, calls)

    def test_auto_switch_is_asked_from_collect(self) -> None:
        start = self.source.index("def _collect")
        end = self.source.index("def _apply(")
        self.assertIn("battery_auto_switch(", self.source[start:end])


if __name__ == "__main__":
    unittest.main()
