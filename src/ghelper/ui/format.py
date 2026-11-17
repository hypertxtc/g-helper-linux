# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure text formatting shared by the window and the tray tooltip."""

from __future__ import annotations

from typing import Any

SEPARATOR = "  \u00b7  "


def temperature(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.0f}\u00b0C"
    except (TypeError, ValueError):
        return None


def _watts(value: Any) -> str | None:
    """Power draw in watts; the sysfs sign depends on the driver, so ignore it."""
    try:
        watts = abs(float(value))
    except (TypeError, ValueError):
        return None
    if watts <= 0:
        return None
    if watts >= 100:
        return f"{watts:.0f} W"
    text = f"{watts:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return f"{text} W"


def _remaining(energy_wh: Any, power_w: Any) -> str | None:
    """Rough runtime from the current charge and draw."""
    try:
        energy = float(energy_wh)
        power = abs(float(power_w))
    except (TypeError, ValueError):
        return None
    if energy <= 0 or power <= 0:
        return None
    minutes = int(round(energy / power * 60))
    if minutes <= 0:
        return None
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def battery_line(battery: dict[str, Any] | None) -> str:
    """Compact battery summary, e.g. ``78% · 12.3 W · 2h 41m``.

    An empty string means there is nothing useful to show, so callers can
    skip it instead of printing a placeholder.
    """
    if not isinstance(battery, dict):
        return ""
    capacity = battery.get("capacity")
    if capacity is None:
        return ""

    parts = [f"{int(capacity)}%"]
    status = str(battery.get("status") or "").strip().lower()
    power = battery.get("power_w")

    if status == "charging":
        parts.append("charging")
        watts = _watts(power)
        if watts:
            parts.append(watts)
    elif status == "discharging":
        watts = _watts(power)
        if watts:
            parts.append(watts)
        remaining = _remaining(battery.get("energy_wh"), power)
        if remaining:
            parts.append(remaining)
    elif status == "full":
        parts.append("full")

    return SEPARATOR.join(parts)


def status_line(
    profile: str,
    cpu_temp: float | int | None,
    gpu_temp: float | int | None,
    ac_online: bool | None = None,
    battery: dict[str, Any] | None = None,
    *,
    gpu_mode: str | None = None,
    pending_reboot: bool = False,
) -> str:
    """One compact line, e.g. ``Balanced · GPU Smart · CPU 57°C · GPU 41°C · AC · 78%``."""
    parts = [profile] if profile else []
    if gpu_mode:
        parts.append(f"GPU {gpu_mode.title()}")
    cpu = temperature(cpu_temp)
    gpu = temperature(gpu_temp)
    if cpu is not None:
        parts.append(f"CPU {cpu}")
    if gpu is not None:
        parts.append(f"GPU {gpu}")
    if ac_online is not None:
        parts.append("AC" if ac_online else "Battery")
    battery_text = battery_line(battery)
    if battery_text:
        parts.append(battery_text)
    if pending_reboot:
        parts.append("Reboot required")
    return SEPARATOR.join(parts)
