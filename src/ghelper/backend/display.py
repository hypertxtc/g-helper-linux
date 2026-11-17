# SPDX-License-Identifier: GPL-3.0-or-later

"""Display helpers.

Refresh-rate switching is desktop specific.  On KDE Plasma/Wayland we drive
``kscreen-doctor``; on anything else the page degrades to read-only instead of
pretending to support it.

``kscreen-doctor`` is a KWin client: on GNOME or a wlroots compositor it is
either missing or fails against a desktop it cannot reach, so the desktop name
gates the backend as well as the binary.
"""

from __future__ import annotations

import logging
import os
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


def desktop_tokens() -> set[str]:
    """Desktop names from the session environment, normalised to upper case."""
    value = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get(
        "XDG_SESSION_DESKTOP", ""
    )
    return {token.strip().upper() for token in re.split(r"[:;]", value) if token.strip()}


def is_kde() -> bool:
    return "KDE" in desktop_tokens()


def available() -> bool:
    """Whether refresh-rate control can work here.

    An unknown desktop (no ``XDG_CURRENT_DESKTOP``, e.g. started by hand) is
    allowed through: the binary is then the only evidence we have, and the
    probe fails honestly if the desktop is wrong.
    """
    if shutil.which("kscreen-doctor") is None:
        return False
    tokens = desktop_tokens()
    return not tokens or "KDE" in tokens


def unavailable_reason() -> str:
    """Short, user-facing explanation for a disabled refresh row."""
    if shutil.which("kscreen-doctor") is None:
        return "Install kscreen-doctor to switch refresh rates"
    return "Refresh-rate switching needs KDE Plasma"


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
