# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for NVML telemetry and sysfs runtime-PM gating."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import nvml, system  # noqa: E402


def _make_pci_dev(
    root: Path,
    slot: str,
    *,
    pci_class: str = "0x030200",
    vendor: str = "0x10de",
    driver: str | None = "nvidia",
    runtime_status: str | None = "active",
    boot_vga: str | None = "0",
) -> Path:
    dev = root / slot
    dev.mkdir()
    (dev / "class").write_text(f"{pci_class}\n", encoding="utf-8")
    (dev / "vendor").write_text(f"{vendor}\n", encoding="utf-8")
    if boot_vga is not None:
        (dev / "boot_vga").write_text(f"{boot_vga}\n", encoding="utf-8")
    if driver is not None:
        os.symlink(f"../../../drivers/{driver}", dev / "driver")
    if runtime_status is not None:
        power = dev / "power"
        power.mkdir()
        (power / "runtime_status").write_text(f"{runtime_status}\n", encoding="utf-8")
    return dev


class SysfsNvidiaTests(unittest.TestCase):
    def test_finds_active_and_unsupported_nvidia_devices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_dev(root, "0000:01:00.0", runtime_status="active")
            _make_pci_dev(root, "0000:02:00.0", runtime_status="unsupported")
            devices = system.nvidia_pci_devices(str(root))
        self.assertEqual(len(devices), 2)
        self.assertTrue(all(d["active"] for d in devices))

    def test_suspended_gpu_is_marked_inactive_and_never_woken(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_dev(root, "0000:01:00.0", runtime_status="suspended")
            devices = system.nvidia_pci_devices(str(root))
            self.assertEqual(len(devices), 1)
            self.assertFalse(devices[0]["active"])

            monitor = object.__new__(system.Monitor)
            monitor._hwmon = {}
            with (
                mock.patch.object(system, "PCI_DEVICES", str(root)),
                mock.patch.object(system.nvml, "read_gpu") as read_gpu,
            ):
                temps = monitor.gpu_temps()
                power = monitor.gpu_power_w()
        self.assertIsNone(temps["dgpu"])
        self.assertIsNone(power)
        read_gpu.assert_not_called()

    def test_skips_nouveau_and_non_nvidia_devices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_dev(root, "0000:01:00.0", driver="nouveau")
            _make_pci_dev(root, "0000:03:00.0", vendor="0x1002", driver="amdgpu")
            _make_pci_dev(root, "0000:04:00.0", pci_class="0x020000", driver="nvidia")
            devices = system.nvidia_pci_devices(str(root))
        self.assertEqual(devices, [])


class FakeNvmlLib:
    def __init__(
        self,
        *,
        init_rc: int = 0,
        handle_rc: int = 0,
        temp_rc: int = 0,
        temp_c: int = 58,
        power_rc: int = 0,
        power_mw: int = 24500,
    ) -> None:
        self.init_rc = init_rc
        self.handle_rc = handle_rc
        self.temp_rc = temp_rc
        self.temp_c = temp_c
        self.power_rc = power_rc
        self.power_mw = power_mw
        self.shutdown_calls = 0

        def init_fn() -> int:
            return self.init_rc

        def shutdown_fn() -> int:
            self.shutdown_calls += 1
            return 0

        def handle_fn(_pci, out_handle) -> int:
            if self.handle_rc == 0:
                out_handle._obj.value = 0x1234
            return self.handle_rc

        def temp_fn(_handle, _sensor, out_temp) -> int:
            if self.temp_rc == 0:
                out_temp._obj.value = self.temp_c
            return self.temp_rc

        def power_fn(_handle, out_power) -> int:
            if self.power_rc == 0:
                out_power._obj.value = self.power_mw
            return self.power_rc

        self.nvmlInit_v2 = mock.Mock(side_effect=init_fn)
        self.nvmlShutdown = mock.Mock(side_effect=shutdown_fn)
        self.nvmlDeviceGetHandleByPciBusId_v2 = mock.Mock(side_effect=handle_fn)
        self.nvmlDeviceGetTemperature = mock.Mock(side_effect=temp_fn)
        self.nvmlDeviceGetPowerUsage = mock.Mock(side_effect=power_fn)


class NvmlReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        nvml.reset_cache()

    def tearDown(self) -> None:
        nvml.reset_cache()

    def test_reads_temp_and_power_and_shuts_down(self) -> None:
        fake = FakeNvmlLib(temp_c=63, power_mw=41250)
        with mock.patch.object(nvml.ctypes, "CDLL", return_value=fake):
            sample = nvml.read_gpu("0000:01:00.0")
        self.assertEqual(sample, {"temp": 63.0, "power_w": 41.25})
        self.assertEqual(fake.shutdown_calls, 1)

    def test_power_unsupported_keeps_temperature(self) -> None:
        fake = FakeNvmlLib(temp_c=54, power_rc=3)
        with mock.patch.object(nvml.ctypes, "CDLL", return_value=fake):
            sample = nvml.read_gpu("0000:01:00.0")
        self.assertEqual(sample, {"temp": 54.0, "power_w": None})
        self.assertEqual(fake.shutdown_calls, 1)

    def test_init_failure_returns_none(self) -> None:
        fake = FakeNvmlLib(init_rc=1)
        with mock.patch.object(nvml.ctypes, "CDLL", return_value=fake):
            self.assertIsNone(nvml.read_gpu("0000:01:00.0"))
        self.assertEqual(fake.shutdown_calls, 0)

    def test_missing_library_is_cached(self) -> None:
        with mock.patch.object(nvml.ctypes, "CDLL", side_effect=OSError("missing")) as cdll:
            self.assertFalse(nvml.available())
            self.assertIsNone(nvml.read_gpu("0000:01:00.0"))
        self.assertEqual(cdll.call_count, len(nvml.LIB_NAMES))

    def test_snapshot_reads_nvidia_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_dev(root, "0000:01:00.0", runtime_status="active")
            monitor = object.__new__(system.Monitor)
            monitor._hwmon = {}
            monitor._last_cpu_times = None
            monitor._battery = None
            with (
                mock.patch.object(system, "PCI_DEVICES", str(root)),
                mock.patch.object(
                    system.nvml,
                    "read_gpu",
                    return_value={"temp": 61.0, "power_w": 35.0},
                ) as read_gpu,
                mock.patch.object(system, "_ac_online", return_value=True),
                mock.patch.object(monitor, "cpu_load", return_value=10.0),
                mock.patch.object(monitor, "cpu_mhz", return_value=3200.0),
            ):
                snap = monitor.snapshot()
        read_gpu.assert_called_once_with("0000:01:00.0")
        self.assertEqual(snap["gpu"]["dgpu"], 61.0)
        self.assertEqual(snap["gpu_power"], 35.0)

    def test_ultimate_mux_amd_igpu_plus_nvidia_dgpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pci"
            root.mkdir()
            _make_pci_dev(
                root, "0000:01:00.0", vendor="0x10de", driver="nvidia",
                runtime_status="active", boot_vga="1",
            )
            amd_dev = _make_pci_dev(
                root, "0000:65:00.0", pci_class="0x030000", vendor="0x1002",
                driver="amdgpu", runtime_status=None, boot_vga="0",
            )
            hwmon = Path(tmp) / "hwmon0"
            hwmon.mkdir()
            (hwmon / "temp1_input").write_text("41000\n", encoding="utf-8")
            (hwmon / "power1_input").write_text("6473000\n", encoding="utf-8")
            os.symlink(amd_dev, hwmon / "device")

            monitor = object.__new__(system.Monitor)
            monitor._hwmon = {"amdgpu": [str(hwmon)]}
            monitor._last_cpu_times = None
            monitor._battery = None
            with (
                mock.patch.object(system, "PCI_DEVICES", str(root)),
                mock.patch.object(
                    system.nvml,
                    "read_gpu",
                    return_value={"temp": 55.0, "power_w": 28.5},
                ) as read_gpu,
                mock.patch.object(system, "_ac_online", return_value=True),
                mock.patch.object(monitor, "cpu_load", return_value=5.0),
                mock.patch.object(monitor, "cpu_mhz", return_value=2100.0),
            ):
                snap = monitor.snapshot()
        read_gpu.assert_called_once_with("0000:01:00.0")
        self.assertEqual(snap["gpu"]["igpu"], 41.0)
        self.assertEqual(snap["gpu"]["dgpu"], 55.0)
        self.assertTrue(snap["gpu"]["pci"]["0000:65:00.0"]["igpu"])
        self.assertFalse(snap["gpu"]["pci"]["0000:01:00.0"]["igpu"])
        self.assertEqual(snap["gpu_power"], 28.5)

    def test_suspended_nvidia_with_amd_igpu_falls_back_to_igpu_power_without_waking(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pci"
            root.mkdir()
            _make_pci_dev(
                root, "0000:01:00.0", vendor="0x10de", driver="nvidia",
                runtime_status="suspended", boot_vga="0",
            )
            amd_dev = _make_pci_dev(
                root, "0000:65:00.0", pci_class="0x030000", vendor="0x1002",
                driver="amdgpu", runtime_status=None, boot_vga="1",
            )
            hwmon = Path(tmp) / "hwmon0"
            hwmon.mkdir()
            (hwmon / "temp1_input").write_text("42000\n", encoding="utf-8")
            (hwmon / "power1_input").write_text("5500000\n", encoding="utf-8")
            os.symlink(amd_dev, hwmon / "device")

            monitor = object.__new__(system.Monitor)
            monitor._hwmon = {"amdgpu": [str(hwmon)]}
            monitor._last_cpu_times = None
            monitor._battery = None
            with (
                mock.patch.object(system, "PCI_DEVICES", str(root)),
                mock.patch.object(system.nvml, "read_gpu") as read_gpu,
                mock.patch.object(system, "_ac_online", return_value=False),
                mock.patch.object(monitor, "cpu_load", return_value=5.0),
                mock.patch.object(monitor, "cpu_mhz", return_value=1400.0),
            ):
                snap = monitor.snapshot()
        read_gpu.assert_not_called()
        self.assertEqual(snap["gpu"]["igpu"], 42.0)
        self.assertIsNone(snap["gpu"]["dgpu"])
        self.assertAlmostEqual(snap["gpu_power"], 5.5)


if __name__ == "__main__":
    unittest.main()
