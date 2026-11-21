# SPDX-License-Identifier: GPL-3.0-or-later

"""Parser tests that do not need asusd, cardwire or a display."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import asusd, cardwire, display, system  # noqa: E402


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


class DisplayControllerTests(unittest.TestCase):
    def test_counts_display_classes(self) -> None:
        self.assertEqual(
            system.count_display_controllers(
                ["0x030000", "0x030000", "0x020000", "0x060000"]
            ),
            2,
        )
        self.assertEqual(system.count_display_controllers(["0x030000"]), 1)
        self.assertEqual(system.count_display_controllers([]), 0)

    def test_junk_classes_are_ignored(self) -> None:
        self.assertEqual(
            system.count_display_controllers(["nonsense", "", None, "0x030000"]), 1
        )

    def test_3d_controllers_count(self) -> None:
        self.assertEqual(
            system.count_display_controllers(["0x030200", "0x038000"]), 2
        )


class RescanTests(unittest.TestCase):
    HELPER = Path("/usr/libexec/ghelper-linux/rescan-pci")

    def _run(self, *, helper: bool, returncode: int = 0, stderr: str = ""):
        found = self.HELPER if helper else None
        with mock.patch.object(
            system.shutil, "which", return_value="/usr/bin/pkexec"
        ), mock.patch.object(
            system, "rescan_helper", return_value=found
        ), mock.patch.object(
            system.subprocess, "run"
        ) as run:
            run.return_value = mock.Mock(returncode=returncode, stdout="", stderr=stderr)
            try:
                system.rescan_pci()
                result = None
            except OSError as exc:
                result = str(exc)
        return run, result

    def test_uses_installed_helper(self) -> None:
        run, result = self._run(helper=True)
        self.assertIsNone(result)
        self.assertEqual(
            run.call_args.args[0], ["/usr/bin/pkexec", str(self.HELPER)]
        )

    def test_falls_back_when_helper_is_missing(self) -> None:
        run, result = self._run(helper=False)
        self.assertIsNone(result)
        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/pkexec", "sh", "-c", "echo 1 > /sys/bus/pci/rescan"],
        )

    def test_cancelled_authentication(self) -> None:
        _run, result = self._run(helper=True, returncode=126)
        self.assertEqual(result, "Authentication cancelled")

    def test_failure_reports_last_line(self) -> None:
        _run, result = self._run(helper=True, returncode=1, stderr="line one\nboom\n")
        self.assertEqual(result, "boom")

    def test_missing_pkexec(self) -> None:
        with mock.patch.object(system.shutil, "which", return_value=None):
            with self.assertRaises(OSError):
                system.rescan_pci()


class RescanHelperTests(unittest.TestCase):
    def test_launcher_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / system.RESCAN_HELPER_NAME
            helper.write_text("#!/bin/sh\n", encoding="utf-8")
            with mock.patch.dict(
                system.os.environ, {"GHELPER_LIBEXECDIR": tmp}, clear=False
            ):
                self.assertEqual(system.rescan_helper(), helper)

    def test_override_without_helper_falls_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            system.os.environ, {"GHELPER_LIBEXECDIR": tmp}, clear=False
        ), mock.patch.object(system, "HELPER_DIRS", ()):
            self.assertIsNone(system.rescan_helper())

    def test_unpackaged_checkout_has_no_helper(self) -> None:
        with mock.patch.dict(system.os.environ, {}, clear=True), mock.patch.object(
            system, "HELPER_DIRS", ()
        ):
            self.assertIsNone(system.rescan_helper())


class GpuTemperatureTests(unittest.TestCase):
    def _monitor(self, hwmon: dict[str, list[str]]) -> "system.Monitor":
        # Bypass __init__: these tests only need the hwmon map.
        monitor = object.__new__(system.Monitor)
        monitor._hwmon = hwmon
        return monitor

    def test_nouveau_reports_the_dgpu_when_amdgpu_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "temp1_input").write_text("52000\n", encoding="utf-8")
            monitor = self._monitor({"nouveau": [tmp]})
            with mock.patch.object(system, "_pci_address", return_value=None):
                temps = monitor.gpu_temps()
        self.assertEqual(temps["dgpu"], 52.0)
        self.assertIsNone(temps["igpu"])

    def test_nouveau_is_ignored_when_amdgpu_has_a_dgpu(self) -> None:
        with tempfile.TemporaryDirectory() as amd, tempfile.TemporaryDirectory() as nv:
            Path(amd, "temp1_input").write_text("61000\n", encoding="utf-8")
            Path(nv, "temp1_input").write_text("52000\n", encoding="utf-8")
            monitor = self._monitor({"amdgpu": [amd], "nouveau": [nv]})
            # No PCI address means no boot_vga flag, so the amdgpu is the dGPU.
            with mock.patch.object(system, "_pci_address", return_value=None):
                temps = monitor.gpu_temps()
        self.assertEqual(temps["dgpu"], 61.0)


class ArmouryAttributeTests(unittest.TestCase):
    def test_every_logical_attribute_has_an_alias(self) -> None:
        logical = (
            asusd.ATTR_DGPU_DISABLE,
            asusd.ATTR_GPU_MUX,
            asusd.ATTR_PANEL_OVERDRIVE,
            asusd.ATTR_PPT_APU,
            asusd.ATTR_PPT_PLATFORM,
            asusd.ATTR_PPT_FPPT,
            asusd.ATTR_PENDING_REBOOT,
            asusd.ATTR_BOOT_SOUND,
        )
        for name in logical:
            with self.subTest(attribute=name):
                self.assertIn(name, asusd.ATTRIBUTE_ALIASES)
                self.assertEqual(asusd.ATTRIBUTE_ALIASES[name][0], name)

    def test_boot_sound_uses_the_firmware_name(self) -> None:
        self.assertEqual(
            asusd.ATTRIBUTE_ALIASES[asusd.ATTR_BOOT_SOUND], ("boot_sound",)
        )


class BatteryTests(unittest.TestCase):
    def _read_battery(self, values: dict[str, str]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name, content in values.items():
                (base / name).write_text(f"{content}\n", encoding="utf-8")
            monitor = object.__new__(system.Monitor)
            monitor._battery = str(base)
            return monitor.battery() or {}

    def test_charge_based_battery_health_and_wattage(self) -> None:
        bat = self._read_battery(
            {
                "capacity": "80",
                "status": "Discharging",
                "charge_now": "3600000",
                "charge_full": "4500000",
                "charge_full_design": "5000000",
                "current_now": "-1500000",
                "voltage_now": "16000000",
                "voltage_min_design": "15400000",
                "cycle_count": "12",
                "charge_control_end_threshold": "80",
            }
        )
        self.assertEqual(bat["capacity"], 80)
        self.assertEqual(bat["status"], "Discharging")
        self.assertAlmostEqual(bat["power_w"], 24.0)
        self.assertAlmostEqual(bat["energy_wh"], 57.6)
        self.assertAlmostEqual(bat["full_wh"], 72.0)
        self.assertAlmostEqual(bat["design_wh"], 77.0)
        # Uses direct uAh ratio (4.5M / 5.0M = 90%) regardless of voltage delta.
        self.assertAlmostEqual(bat["health"], 90.0)
        self.assertEqual(bat["cycles"], 12)
        self.assertEqual(bat["limit"], 80)

    def test_charge_based_battery_zero_current_and_voltage_fallback(self) -> None:
        bat = self._read_battery(
            {
                "capacity": "100",
                "status": "Full",
                "charge_now": "5000000",
                "charge_full": "5000000",
                "charge_full_design": "5200000",
                "current_now": "0",
                "voltage_now": "16000000",
            }
        )
        self.assertIsNone(bat["power_w"])
        self.assertAlmostEqual(bat["design_wh"], 83.2)
        self.assertAlmostEqual(bat["health"], 5000000 * 100.0 / 5200000)

    def test_energy_based_battery_preserved(self) -> None:
        bat = self._read_battery(
            {
                "capacity": "92",
                "status": "Discharging",
                "power_now": "13590000",
                "energy_now": "52881000",
                "energy_full": "57486000",
                "energy_full_design": "76000000",
                "voltage_now": "15933000",
                "cycle_count": "0",
                "charge_control_end_threshold": "100",
            }
        )
        self.assertAlmostEqual(bat["power_w"], 13.59)
        self.assertAlmostEqual(bat["energy_wh"], 52.881)
        self.assertAlmostEqual(bat["full_wh"], 57.486)
        self.assertAlmostEqual(bat["design_wh"], 76.0)
        self.assertAlmostEqual(bat["health"], 100.0 * 57486000 / 76000000)


if __name__ == "__main__":
    unittest.main()
