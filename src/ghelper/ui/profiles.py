# SPDX-License-Identifier: GPL-3.0-or-later

"""Performance-mode names, icons and accent colours.

Every window used to keep its own copy of the label table, which is how the
overlay would eventually disagree with the buttons.  One table, one answer.

The third field is the accent colour class used by the mode buttons and the
live status line; the overlay ignores it.
"""

from __future__ import annotations

from ..backend import asusd as asusd_mod

PROFILE_META: dict[int, tuple[str, str, str]] = {
    asusd_mod.QUIET: ("Silent", "gh-silent-symbolic", "green"),
    asusd_mod.LOW_POWER: ("Silent", "gh-silent-symbolic", "green"),
    asusd_mod.BALANCED: ("Balanced", "gh-balanced-symbolic", "blue"),
    asusd_mod.PERFORMANCE: ("Turbo", "gh-turbo-symbolic", "red"),
    asusd_mod.CUSTOM: ("Custom", "gh-optimized-symbolic", "orange"),
}

PROFILE_LABELS = {
    profile: label for profile, (label, _icon, _accent) in PROFILE_META.items()
}


def label(profile: int) -> str:
    """Human name for a profile, falling back to asusd's own name."""
    meta = PROFILE_META.get(profile)
    if meta is not None:
        return meta[0]
    return asusd_mod.PROFILE_NAMES.get(profile, str(profile))


def icon_name(profile: int, *, dark: bool = False) -> str:
    """Icon theme name for a profile, optionally forced to the dark variant.

    The overlay is dark whatever the desktop theme is doing, so it asks for the
    dark PNGs instead of the theme-aware helper used inside the windows.
    """
    meta = PROFILE_META.get(profile)
    name = meta[1].removesuffix("-symbolic") if meta else "gh-balanced"
    return f"{name}-dark" if dark else name
