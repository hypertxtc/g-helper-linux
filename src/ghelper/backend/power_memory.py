# SPDX-License-Identifier: GPL-3.0-or-later

"""Remember the performance mode used on each power source.

asusd can apply a profile automatically when the cable is plugged or pulled,
but it only knows the two values stored in its own ``PlatformProfileOnAc`` and
``PlatformProfileOnBattery`` properties.  G-Helper on Windows instead remembers
whatever mode was *active* on AC and on battery, so the choice follows the user
without a second trip to a settings page.

This module mirrors an explicit profile change into the slot for the current
power source.  asusd keeps doing the switching, which means it still works
while this application is closed, and the settings page simply shows the two
remembered values.

Only explicit choices made here go through ``remember()``.  Changes asusd makes
by itself are already stored in those slots, and mirroring them back would at
best be a no-op and at worst overwrite a choice made a moment earlier.
"""

from __future__ import annotations

import logging

from .system import ac_online

log = logging.getLogger(__name__)


def target(asusd, ac: bool) -> int | None:
    """The mode asusd is going to apply for ``ac``, if it switches at all.

    ``None`` means "no automatic switch", in which case the caller should keep
    showing whatever profile is actually active.  Profile 0 is Balanced, so a
    plain truth test here would be a bug.
    """
    if ac:
        return asusd.profile_on_ac() if asusd.change_profile_on_ac() else None
    return asusd.profile_on_battery() if asusd.change_profile_on_battery() else None


def remember(asusd, profile: int, ac: bool | None = None) -> str | None:
    """Store ``profile`` as the preference for the current power source.

    Returns ``"ac"`` or ``"battery"``, or ``None`` when the power source is
    unknown.  Failures are logged and swallowed: the profile itself has already
    been applied, so a stale side preference is not worth an error dialog.
    """
    if ac is None:
        ac = ac_online()
    if ac is None:
        log.debug("power source unknown; not remembering profile %s", profile)
        return None

    slot = "ac" if ac else "battery"
    try:
        if ac:
            asusd.set_profile_on_ac(profile)
        else:
            asusd.set_profile_on_battery(profile)
    except Exception as exc:  # noqa: BLE001 - never fail the profile switch
        log.warning("could not remember profile for %s: %s", slot, exc)
        return None
    log.debug("remembered profile %s for %s", profile, slot)
    return slot
