# SPDX-License-Identifier: GPL-3.0-or-later

"""Display helpers.

Refresh-rate switching is desktop specific.  On KDE Plasma/Wayland we drive
``kscreen-doctor``; on anything else the page degrades to read-only instead of
pretending to support it.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MODE = re.compile(r"(\d+):(\d+)x(\d+)@([\d.]+)([*!]*)")
_GEOMETRY = re.compile(r"(\d+)x(\d+)")


class DisplayError(RuntimeError):
    pass


def available() -> bool:
    return shutil.which("kscreen-doctor") is not None


def parse_outputs(text: str) -> list[dict[str, Any]]:
    """Parse ``kscreen-doctor -o`` output (ANSI escapes tolerated)."""
    result: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = _ANSI.sub("", raw_line).strip()
        if line.startswith("Output:"):
            parts = line.split()
            current = {
                "name": parts[2] if len(parts) > 2 else "unknown",
                "enabled": False,
                "panel": False,
                "modes": [],
                "scale": None,
                "geometry": None,
            }
            result.append(current)
            continue
        if current is None:
            continue
        if line == "enabled":
            current["enabled"] = True
        elif line == "Panel":
            current["panel"] = True
        elif line.startswith("Modes:"):
            for match in _MODE.finditer(line):
                current["modes"].append(
                    {
                        "id": int(match.group(1)),
                        "width": int(match.group(2)),
                        "height": int(match.group(3)),
                        "rate": float(match.group(4)),
                        "current": "*" in match.group(5),
                        "preferred": "!" in match.group(5),
                    }
                )
        elif line.startswith("Scale:"):
            try:
                current["scale"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Geometry:"):
            match = _GEOMETRY.search(line)
            if match:
                current["geometry"] = (int(match.group(1)), int(match.group(2)))
    return result


def outputs() -> list[dict[str, Any]]:
    if not available():
        return []
    try:
        proc = subprocess.run(
            ["kscreen-doctor", "-o"], capture_output=True, text=True, timeout=8, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DisplayError(str(exc)) from exc
    if proc.returncode != 0:
        raise DisplayError((proc.stderr or "kscreen-doctor failed").strip())
    return parse_outputs(proc.stdout)


def primary_panel() -> dict[str, Any] | None:
    candidates = [o for o in outputs() if o["enabled"]]
    for output in candidates:
        if output["panel"]:
            return output
    return candidates[0] if candidates else None


def current_mode(output: dict[str, Any]) -> dict[str, Any] | None:
    for mode in output["modes"]:
        if mode["current"]:
            return mode
    return None


def refresh_rates(output: dict[str, Any], mode: dict[str, Any] | None = None) -> list[float]:
    mode = mode or current_mode(output)
    if mode is None:
        return []
    rates = {
        m["rate"]
        for m in output["modes"]
        if m["width"] == mode["width"] and m["height"] == mode["height"]
    }
    return sorted(rates, reverse=True)


def set_refresh_rate(output_name: str, width: int, height: int, rate: float) -> None:
    if not available():
        raise DisplayError("kscreen-doctor is not available")
    output = next((o for o in outputs() if o["name"] == output_name), None)
    if output is None:
        raise DisplayError(f"Output {output_name} not found")
    matches = [
        m
        for m in output["modes"]
        if m["width"] == width and m["height"] == height and abs(m["rate"] - rate) < 0.3
    ]
    if not matches:
        raise DisplayError(f"{width}x{height}@{rate:g} is not a valid mode")
    mode = matches[0]
    try:
        proc = subprocess.run(
            ["kscreen-doctor", f"output.{output_name}.mode.{mode['id']}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DisplayError(str(exc)) from exc
    if proc.returncode != 0:
        raise DisplayError((proc.stderr or "Could not change refresh rate").strip())
