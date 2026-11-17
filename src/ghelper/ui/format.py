# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure text formatting shared by the window and the tray tooltip."""

from __future__ import annotations

SEPARATOR = "  \u00b7  "


def temperature(value: float | int | None) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.0f}\u00b0C"
    except (TypeError, ValueError):
        return None


def status_line(
    profile: str,
    cpu_temp: float | int | None,
    gpu_temp: float | int | None,
    ac_online: bool | None = None,
) -> str:
    """One compact line, e.g. ``Balanced · CPU 57°C · GPU 41°C · AC``."""
    parts = [profile] if profile else []
    cpu = temperature(cpu_temp)
    gpu = temperature(gpu_temp)
    if cpu is not None:
        parts.append(f"CPU {cpu}")
    if gpu is not None:
        parts.append(f"GPU {gpu}")
    if ac_online is not None:
        parts.append("AC" if ac_online else "Battery")
    return SEPARATOR.join(parts)
