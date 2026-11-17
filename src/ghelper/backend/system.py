# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only hardware telemetry from sysfs: temperatures, fans, battery, load."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

HWMON_ROOT = "/sys/class/hwmon"
POWER_SUPPLY = "/sys/class/power_supply"
PCI_DEVICES = "/sys/bus/pci/devices"
CPU_FREQ = "/sys/devices/system/cpu/cpu0/cpufreq"


def read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


# Internal alias kept short for the many sysfs reads below.
_text = read_text


def _int(path: str) -> int | None:
    value = _text(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def hwmon_devices() -> dict[str, list[str]]:
    """Map hwmon chip name -> list of hwmon directories."""
    found: dict[str, list[str]] = {}
    try:
        entries = sorted(os.listdir(HWMON_ROOT))
    except OSError:
        return found
    for entry in entries:
        path = os.path.join(HWMON_ROOT, entry)
        name = _text(os.path.join(path, "name"))
        if name:
            found.setdefault(name, []).append(path)
    return found


def _pci_address(hwmon_path: str) -> str | None:
    try:
        resolved = os.path.realpath(os.path.join(hwmon_path, "device"))
    except OSError:
        return None
    base = os.path.basename(resolved)
    return base if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]", base) else None


def machine_name() -> str:
    for path in ("/sys/class/dmi/id/product_name", "/sys/class/dmi/id/board_name"):
        value = _text(path)
        if value:
            words = value.replace("_", " ").split()
            # model + board names are often doubled: "GA402RK GA402RK"
            deduped: list[str] = []
            for word in words:
                if not deduped or deduped[-1] != word:
                    deduped.append(word)
            return " ".join(deduped).strip()
    return "ASUS laptop"


def cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "CPU"


def _cpu_times() -> tuple[int, int] | None:
    try:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            fields = handle.readline().split()[1:]
        values = [int(v) for v in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle
    except (OSError, ValueError):
        return None


def _battery_path() -> str | None:
    try:
        entries = sorted(os.listdir(POWER_SUPPLY))
    except OSError:
        return None
    for entry in entries:
        if entry.startswith("BAT"):
            return os.path.join(POWER_SUPPLY, entry)
    return None


def ac_online() -> bool | None:
    """Whether an AC/ADP supply reports online, if the machine exposes one."""
    try:
        entries = sorted(os.listdir(POWER_SUPPLY))
    except OSError:
        return None
    for entry in entries:
        if entry.startswith("AC") or entry.startswith("ADP"):
            value = _int(os.path.join(POWER_SUPPLY, entry, "online"))
            if value is not None:
                return bool(value)
    return None


def count_display_controllers(classes: "list[str]") -> int:
    """Count PCI display controllers from their ``class`` values."""
    count = 0
    for value in classes:
        try:
            code = int(str(value).strip(), 16)
        except (TypeError, ValueError):
            continue
        if code >> 16 == 0x03:
            count += 1
    return count


def display_controller_count() -> int | None:
    try:
        entries = os.listdir(PCI_DEVICES)
    except OSError:
        return None
    classes = []
    for entry in entries:
        try:
            with open(
                os.path.join(PCI_DEVICES, entry, "class"), "r", encoding="utf-8"
            ) as handle:
                classes.append(handle.read())
        except OSError:
            continue
    return count_display_controllers(classes)


def dgpu_present() -> bool | None:
    """Whether more than one display controller is on the PCI bus.

    A firmware ECO switch removes the discrete GPU entirely, and the
    ``DgpuDisable`` attribute can already read 0 while the GPU stays off until
    the next boot.  Counting controllers is therefore the reliable check.
    """
    count = display_controller_count()
    if not count:
        return None
    return count > 1


def _ac_online() -> bool | None:
    return ac_online()


class Monitor:
    """Snapshot-oriented telemetry reader.

    ``snapshot()`` is cheap enough to call every couple of seconds from the
    GTK main loop; it only touches sysfs and /proc.
    """

    def __init__(self) -> None:
        self._hwmon = hwmon_devices()
        self._last_cpu_times = _cpu_times()
        self._battery = _battery_path()

    def refresh_devices(self) -> None:
        self._hwmon = hwmon_devices()
        self._battery = _battery_path()

    # -- individual sensors --------------------------------------------
    def cpu_temp(self) -> tuple[float | None, str]:
        path = self._hwmon.get("k10temp") or self._hwmon.get("coretemp")
        if path:
            value = _int(os.path.join(path[0], "temp1_input"))
            if value is not None:
                label = _text(os.path.join(path[0], "temp1_label")) or "CPU"
                return value / 1000.0, label
        for alternative in ("acpitz", "zenpower"):
            if alternative in self._hwmon:
                value = _int(os.path.join(self._hwmon[alternative][0], "temp1_input"))
                if value is not None:
                    return value / 1000.0, "CPU"
        return None, "CPU"

    def gpu_temps(self) -> dict[str, Any]:
        result: dict[str, Any] = {"igpu": None, "dgpu": None, "dgpu_junction": None, "pci": {}}
        for path in self._hwmon.get("amdgpu", []):
            pci = _pci_address(path)
            boot_vga = _int(f"/sys/bus/pci/devices/{pci}/boot_vga") if pci else None
            is_igpu = boot_vga == 1
            edge = _int(os.path.join(path, "temp1_input"))
            junction = _int(os.path.join(path, "temp2_input"))
            if pci:
                result["pci"][pci] = {"igpu": is_igpu, "edge": edge, "junction": junction}
            if is_igpu:
                result["igpu"] = edge / 1000.0 if edge is not None else None
            else:
                # A blocked / runtime-suspended dGPU reads EBUSY: report None.
                result["dgpu"] = edge / 1000.0 if edge is not None else None
                result["dgpu_junction"] = junction / 1000.0 if junction is not None else None
        return result

    def fans(self) -> dict[str, int | None]:
        result: dict[str, int | None] = {
            "cpu": None,
            "gpu": None,
            "cpu_pwm": None,
            "gpu_pwm": None,
        }
        for path in self._hwmon.get("asus", []) + self._hwmon.get("asus_custom_fan_curve", []):
            for index in (1, 2):
                label = (_text(os.path.join(path, f"fan{index}_label")) or "").lower()
                value = _int(os.path.join(path, f"fan{index}_input"))
                pwm = _int(os.path.join(path, f"pwm{index}"))
                if value is None and pwm is None:
                    continue
                target = None
                if "cpu" in label:
                    target = "cpu"
                elif "gpu" in label:
                    target = "gpu"
                elif index == 1 and result["cpu"] is None:
                    target = "cpu"
                elif index == 2 and result["gpu"] is None:
                    target = "gpu"
                if target is None:
                    continue
                if value is not None:
                    result[target] = value
                if pwm is not None:
                    result[f"{target}_pwm"] = pwm
        return result

    def gpu_power_w(self) -> float | None:
        """Read the active GPU's package power from hwmon, if exposed."""
        fallback: int | None = None
        for path in self._hwmon.get("amdgpu", []):
            pci = _pci_address(path)
            boot_vga = _int(f"/sys/bus/pci/devices/{pci}/boot_vga") if pci else None
            value = _int(os.path.join(path, "power1_input"))
            if value is None:
                continue
            if boot_vga == 0:
                return value / 1_000_000
            if fallback is None:
                fallback = value
        return fallback / 1_000_000 if fallback is not None else None

    def battery(self) -> dict[str, Any] | None:
        if not self._battery:
            return None
        base = self._battery
        power_now = _int(os.path.join(base, "power_now"))
        energy_now = _int(os.path.join(base, "energy_now"))
        energy_full = _int(os.path.join(base, "energy_full"))
        design = _int(os.path.join(base, "energy_full_design"))
        if energy_now is None:
            charge_now = _int(os.path.join(base, "charge_now"))
            voltage = _int(os.path.join(base, "voltage_now"))
            if charge_now is not None and voltage is not None:
                energy_now = charge_now * voltage // 1_000_000
        if energy_full is None:
            charge_full = _int(os.path.join(base, "charge_full"))
            voltage = _int(os.path.join(base, "voltage_now"))
            if charge_full is not None and voltage is not None:
                energy_full = charge_full * voltage // 1_000_000
        health = None
        if energy_full and design:
            health = 100.0 * energy_full / design
        return {
            "path": base,
            "capacity": _int(os.path.join(base, "capacity")),
            "status": _text(os.path.join(base, "status")) or "Unknown",
            "power_w": power_now / 1_000_000 if power_now else None,
            "energy_wh": energy_now / 1_000_000 if energy_now else None,
            "full_wh": energy_full / 1_000_000 if energy_full else None,
            "design_wh": design / 1_000_000 if design else None,
            "health": health,
            "cycles": _int(os.path.join(base, "cycle_count")),
            "voltage_v": (_int(os.path.join(base, "voltage_now")) or 0) / 1_000_000 or None,
            "limit": _int(os.path.join(base, "charge_control_end_threshold")),
        }

    def cpu_load(self) -> float | None:
        current = _cpu_times()
        if current is None:
            return None
        if self._last_cpu_times is None:
            self._last_cpu_times = current
            return None
        total_delta = current[0] - self._last_cpu_times[0]
        idle_delta = current[1] - self._last_cpu_times[1]
        self._last_cpu_times = current
        if total_delta <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta))

    def cpu_mhz(self) -> float | None:
        value = _int(os.path.join(CPU_FREQ, "scaling_cur_freq"))
        return value / 1000.0 if value else None

    # -- aggregate ------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        cpu_temp, cpu_label = self.cpu_temp()
        return {
            "cpu_temp": cpu_temp,
            "cpu_label": cpu_label,
            "gpu": self.gpu_temps(),
            "fans": self.fans(),
            "battery": self.battery(),
            "ac_online": _ac_online(),
            "gpu_power": self.gpu_power_w(),
            "cpu_load": self.cpu_load(),
            "cpu_mhz": self.cpu_mhz(),
        }
