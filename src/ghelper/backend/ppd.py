# SPDX-License-Identifier: GPL-3.0-or-later

"""Coordinate power profiles with PowerProfiles D-Bus daemons (tuned-ppd/PPD)."""

from __future__ import annotations

import logging
from typing import Any, Callable

import dbus

from .. import dbus_setup  # noqa: F401 - attach main loop and GLib threads
from . import asusd as asusd_mod

log = logging.getLogger(__name__)

PROPS_IFACE = "org.freedesktop.DBus.Properties"
TIMEOUT = 15.0

TARGETS = (
    (
        "org.freedesktop.UPower.PowerProfiles",
        "/org/freedesktop/UPower/PowerProfiles",
        "org.freedesktop.UPower.PowerProfiles",
    ),
    (
        "net.hadess.PowerProfiles",
        "/net/hadess/PowerProfiles",
        "net.hadess.PowerProfiles",
    ),
)

UPOWER_BUS = "org.freedesktop.UPower"
UPOWER_PATH = "/org/freedesktop/UPower"
UPOWER_IFACE = "org.freedesktop.UPower"

PROFILE_TO_PPD = {
    asusd_mod.QUIET: "power-saver",
    asusd_mod.LOW_POWER: "power-saver",
    asusd_mod.BALANCED: "balanced",
    asusd_mod.PERFORMANCE: "performance",
}


def profile_to_ppd(profile: int) -> str | None:
    """Map an asusd PlatformProfile id to a PowerProfiles name."""
    return PROFILE_TO_PPD.get(profile)


def _find_target(bus) -> tuple[str, str, str] | None:
    for target in TARGETS:
        if bus.name_has_owner(target[0]):
            return target
    return None


def active_profile(*, bus=None) -> str | None:
    """Return the active PowerProfiles name, or ``None`` if unavailable."""
    try:
        conn = bus if bus is not None else dbus.SystemBus()
        target = _find_target(conn)
        if target is None:
            return None
        bus_name, path, iface = target
        props = dbus.Interface(conn.get_object(bus_name, path, introspect=False), PROPS_IFACE)
        return str(props.Get(iface, "ActiveProfile", timeout=TIMEOUT))
    except (dbus.DBusException, OSError) as exc:
        log.debug("could not read PowerProfiles active profile: %s", exc)
        return None


def state(*, bus=None) -> dict[str, Any]:
    """Return a diagnostics dictionary describing the active PowerProfiles daemon."""
    try:
        conn = bus if bus is not None else dbus.SystemBus()
        target = _find_target(conn)
        if target is None:
            return {"available": False}
        bus_name, path, iface = target
        props = dbus.Interface(conn.get_object(bus_name, path, introspect=False), PROPS_IFACE)
        raw = props.GetAll(iface, timeout=TIMEOUT)
    except (dbus.DBusException, OSError) as exc:
        return {"available": False, "error": str(exc)}

    profiles_raw = raw.get("Profiles") or ()
    driver = None
    if profiles_raw and "Driver" in profiles_raw[0]:
        driver = str(profiles_raw[0]["Driver"])
    profiles = [str(entry["Profile"]) for entry in profiles_raw if "Profile" in entry]
    return {
        "available": True,
        "service": bus_name,
        "active_profile": str(raw.get("ActiveProfile", "")),
        "driver": driver,
        "profiles": profiles,
    }


def sync_profile(profile: int, *, bus=None) -> bool:
    """Sync ``profile`` to tuned-ppd / power-profiles-daemon if available.

    Returns ``True`` if ``ActiveProfile`` was updated, ``False`` if it already
    matched or if no PPD daemon could be updated.
    """
    wanted = profile_to_ppd(profile)
    if wanted is None:
        return False
    try:
        conn = bus if bus is not None else dbus.SystemBus()
        target = _find_target(conn)
        if target is None:
            return False
        bus_name, path, iface = target
        props = dbus.Interface(conn.get_object(bus_name, path, introspect=False), PROPS_IFACE)
        current = str(props.Get(iface, "ActiveProfile", timeout=TIMEOUT))
        if current == wanted:
            return False
        props.Set(
            iface,
            "ActiveProfile",
            dbus.String(wanted, variant_level=1),
            timeout=TIMEOUT,
        )
        log.debug("synced %s ActiveProfile -> %s", bus_name, wanted)
        return True
    except (dbus.DBusException, OSError) as exc:
        log.debug("could not sync PowerProfiles (%s): %s", wanted, exc)
        return False


def watch_ac_online(callback: Callable[[bool], None], *, bus=None):
    """Invoke ``callback(ac_online)`` when UPower signals OnBattery changes."""
    conn = bus if bus is not None else dbus.SystemBus()

    def _on_changed(interface, changed, _invalidated) -> None:
        if str(interface) != UPOWER_IFACE or "OnBattery" not in changed:
            return
        callback(not bool(changed["OnBattery"]))

    return conn.add_signal_receiver(
        _on_changed,
        signal_name="PropertiesChanged",
        dbus_interface=PROPS_IFACE,
        bus_name=UPOWER_BUS,
        path=UPOWER_PATH,
    )
