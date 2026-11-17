# SPDX-License-Identifier: GPL-3.0-or-later

"""Thin wrapper around the asusd D-Bus API (asusctl 6.x).

The daemon owns all privileged hardware access.  Everything here is an
unprivileged D-Bus call; asusd's D-Bus policy allows the ``wheel`` group.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

import dbus

log = logging.getLogger(__name__)

BUS_NAME = "xyz.ljones.Asusd"
ROOT_PATH = "/xyz/ljones"
ARMOURY_ROOT = ROOT_PATH + "/asus_armoury"

PLATFORM_IFACE = "xyz.ljones.Platform"
FAN_CURVES_IFACE = "xyz.ljones.FanCurves"
ARMOURY_IFACE = "xyz.ljones.AsusArmoury"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

# rog-platform::PlatformProfile.  Fan-curve profiles use the same numeric ids
# for Balanced/Performance/Quiet.
BALANCED = 0
PERFORMANCE = 1
QUIET = 2
LOW_POWER = 3
CUSTOM = 4

PROFILE_NAMES = {
    QUIET: "Silent",
    BALANCED: "Balanced",
    PERFORMANCE: "Turbo",
    LOW_POWER: "Silent",
    CUSTOM: "Custom",
}
PROFILE_ORDER = (QUIET, BALANCED, PERFORMANCE, CUSTOM)

# The fan-curve API uses rog-profiles::Profile ids, which differ from
# PlatformProfile for LowPower and Custom.  asusd treats LowPower as Quiet.
FAN_PROFILE_IDS = {
    BALANCED: 0,
    PERFORMANCE: 1,
    QUIET: 2,
    LOW_POWER: 2,
    CUSTOM: 3,
}


def fan_profile_for(profile: int) -> int:
    """Map a PlatformProfile id to the matching fan-curve profile id."""
    return FAN_PROFILE_IDS.get(profile, profile)

# Armoury attributes we know how to present.  The value is the logical name
# used by the UI; ATTRIBUTE_ALIASES maps it to the firmware attribute names
# found on different models (AMD vs Intel, old vs new kernels).
ATTR_DGPU_DISABLE = "dgpu_disable"
ATTR_GPU_MUX = "gpu_mux_mode"
ATTR_PANEL_OVERDRIVE = "panel_overdrive"
ATTR_PPT_APU = "ppt_apu_sppt"
ATTR_PPT_PLATFORM = "ppt_platform_sppt"
ATTR_PPT_FPPT = "ppt_pl3_fppt"
ATTR_PENDING_REBOOT = "pending_reboot"

ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    ATTR_DGPU_DISABLE: ("dgpu_disable", "gpu_disable"),
    ATTR_GPU_MUX: ("gpu_mux_mode",),
    ATTR_PANEL_OVERDRIVE: ("panel_overdrive",),
    ATTR_PPT_APU: ("ppt_apu_sppt", "ppt_pl1_spl"),
    ATTR_PPT_PLATFORM: ("ppt_platform_sppt", "ppt_pl2_sppt"),
    ATTR_PPT_FPPT: ("ppt_pl3_fppt",),
    ATTR_PENDING_REBOOT: ("pending_reboot",),
}

# Never let one stuck asusd call block the backend worker forever.
TIMEOUT = 15.0


class AsusdError(RuntimeError):
    """Raised when asusd is missing or rejects a request."""


class Asusd:
    def __init__(self) -> None:
        self._bus = dbus.SystemBus()
        self._version: str | None = None
        self._available = False
        self._attr_cache: dict[str, str | None] = {}

    # -- connection -----------------------------------------------------
    def connect(self) -> None:
        try:
            if not self._bus.name_has_owner(BUS_NAME):
                raise AsusdError(
                    "asusd is not running. Install asusctl and start asusd.service."
                )
            self._attr_cache.clear()
            self._version = str(self._props().Get(PLATFORM_IFACE, "Version", timeout=TIMEOUT))
            self._available = True
        except dbus.DBusException as exc:
            raise AsusdError(_dbus_message(exc)) from exc

    @property
    def available(self) -> bool:
        return self._available

    @property
    def version(self) -> str:
        return self._version or "unknown"

    # -- low level helpers ---------------------------------------------
    def _object(self, path: str):
        return self._bus.get_object(BUS_NAME, path, introspect=False)

    def _props(self, path: str = ROOT_PATH):
        return dbus.Interface(self._object(path), PROPS_IFACE)

    def _iface(self, path: str, iface: str):
        return dbus.Interface(self._object(path), iface)

    def _get(self, name: str, path: str = ROOT_PATH) -> Any:
        try:
            return self._props(path).Get(PLATFORM_IFACE, name, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            raise AsusdError(f"Could not read {name}: {_dbus_message(exc)}") from exc

    def _set(self, name: str, value: Any, path: str = ROOT_PATH) -> None:
        # org.freedesktop.DBus.Properties.Set takes a variant. dbus-python
        # only wraps a value if it was constructed with variant_level=1,
        # otherwise the marshaler produces (ssu) instead of (ssv).
        value = _as_variant(value)
        try:
            self._props(path).Set(PLATFORM_IFACE, name, value, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            raise AsusdError(f"Could not set {name}: {_dbus_message(exc)}") from exc

    # -- platform / profiles -------------------------------------------
    def platform_profile(self) -> int:
        return int(self._get("PlatformProfile"))

    def set_platform_profile(self, profile: int) -> None:
        self._set("PlatformProfile", dbus.UInt32(profile))

    def platform_choices(self) -> list[int]:
        try:
            return [int(p) for p in self._get("PlatformProfileChoices")]
        except AsusdError:
            return [QUIET, BALANCED, PERFORMANCE]

    def next_platform_profile(self) -> None:
        self._call(
            "NextPlatformProfile",
            lambda: self._iface(ROOT_PATH, PLATFORM_IFACE).NextPlatformProfile(timeout=TIMEOUT),
        )

    def linked_epp(self) -> bool:
        return bool(self._get("PlatformProfileLinkedEpp"))

    def set_linked_epp(self, linked: bool) -> None:
        self._set("PlatformProfileLinkedEpp", dbus.Boolean(linked))

    def profile_on_ac(self) -> int:
        return int(self._get("PlatformProfileOnAc"))

    def set_profile_on_ac(self, profile: int) -> None:
        self._set("PlatformProfileOnAc", dbus.UInt32(profile))

    def profile_on_battery(self) -> int:
        return int(self._get("PlatformProfileOnBattery"))

    def set_profile_on_battery(self, profile: int) -> None:
        self._set("PlatformProfileOnBattery", dbus.UInt32(profile))

    def change_profile_on_ac(self) -> bool:
        return bool(self._get("ChangePlatformProfileOnAc"))

    def set_change_profile_on_ac(self, enabled: bool) -> None:
        self._set("ChangePlatformProfileOnAc", dbus.Boolean(enabled))

    def change_profile_on_battery(self) -> bool:
        return bool(self._get("ChangePlatformProfileOnBattery"))

    def set_change_profile_on_battery(self, enabled: bool) -> None:
        self._set("ChangePlatformProfileOnBattery", dbus.Boolean(enabled))

    def enable_ppt_group(self) -> bool:
        return bool(self._get("EnablePptGroup"))

    def set_enable_ppt_group(self, enabled: bool) -> None:
        self._set("EnablePptGroup", dbus.Boolean(enabled))

    # -- per-profile EPP -------------------------------------------------
    EPP_PROPERTIES = {
        QUIET: "ProfileQuietEpp",
        LOW_POWER: "ProfileQuietEpp",
        BALANCED: "ProfileBalancedEpp",
        PERFORMANCE: "ProfilePerformanceEpp",
        CUSTOM: "ProfileCustomEpp",
    }

    def profile_epp(self, profile: int) -> int:
        prop = self.EPP_PROPERTIES.get(profile, "ProfileBalancedEpp")
        return int(self._get(prop))

    def set_profile_epp(self, profile: int, epp: int) -> None:
        prop = self.EPP_PROPERTIES.get(profile, "ProfileBalancedEpp")
        self._set(prop, dbus.UInt32(epp))

    # -- battery --------------------------------------------------------
    def charge_limit(self) -> int:
        return int(self._get("ChargeControlEndThreshold"))

    def set_charge_limit(self, limit: int) -> None:
        self._set("ChargeControlEndThreshold", dbus.Byte(max(20, min(100, int(limit)))))

    def one_shot_full_charge(self) -> None:
        self._call(
            "OneShotFullCharge",
            lambda: self._iface(ROOT_PATH, PLATFORM_IFACE).OneShotFullCharge(
                timeout=TIMEOUT
            ),
        )

    # -- fan curves -----------------------------------------------------
    def fan_curves(self, profile: int) -> dict[str, dict[str, Any]]:
        """Return ``{fan_name: {"pwm": [...], "temp": [...], "enabled": bool}}``."""
        try:
            raw = self._iface(ROOT_PATH, FAN_CURVES_IFACE).FanCurveData(
                dbus.UInt32(profile), timeout=TIMEOUT
            )
        except dbus.DBusException as exc:
            raise AsusdError(f"Could not read fan curves: {_dbus_message(exc)}") from exc
        curves: dict[str, dict[str, Any]] = {}
        for entry in raw:
            name = str(entry[0])
            curves[name] = {
                "pwm": [int(v) for v in entry[1]],
                "temp": [int(v) for v in entry[2]],
                "enabled": bool(entry[3]),
            }
        return curves

    def set_fan_curve(
        self,
        profile: int,
        fan: str,
        pwm: Sequence[int],
        temp: Sequence[int],
        enabled: bool,
    ) -> None:
        if len(pwm) != 8 or len(temp) != 8:
            raise AsusdError("A fan curve needs exactly 8 points.")
        data = dbus.Struct(
            (
                str(fan),
                dbus.Struct([dbus.Byte(v) for v in pwm], signature="yyyyyyyy"),
                dbus.Struct([dbus.Byte(v) for v in temp], signature="yyyyyyyy"),
                dbus.Boolean(enabled),
            ),
            signature="s(yyyyyyyy)(yyyyyyyy)b",
        )
        try:
            self._iface(ROOT_PATH, FAN_CURVES_IFACE).SetFanCurve(
                dbus.UInt32(profile), data, timeout=TIMEOUT
            )
        except dbus.DBusException as exc:
            raise AsusdError(f"Could not write fan curve: {_dbus_message(exc)}") from exc

    def set_fan_curves_enabled(self, profile: int, enabled: bool) -> None:
        self._call(
            "Could not toggle fan curves",
            lambda: self._iface(ROOT_PATH, FAN_CURVES_IFACE).SetFanCurvesEnabled(
                dbus.UInt32(profile), dbus.Boolean(enabled), timeout=TIMEOUT
            ),
        )

    def set_profile_fan_curve_enabled(self, profile: int, fan: str, enabled: bool) -> None:
        self._call(
            "Could not toggle fan curve",
            lambda: self._iface(ROOT_PATH, FAN_CURVES_IFACE).SetProfileFanCurveEnabled(
                dbus.UInt32(profile), str(fan), dbus.Boolean(enabled), timeout=TIMEOUT
            ),
        )

    def reset_fan_curves(self, profile: int) -> None:
        self._call(
            "Could not reset fan curves",
            lambda: self._iface(ROOT_PATH, FAN_CURVES_IFACE).SetCurvesToDefaults(
                dbus.UInt32(profile), timeout=TIMEOUT
            ),
        )

    # -- armoury / BIOS attributes -------------------------------------
    def _armoury_get(self, name: str) -> dict[str, Any]:
        path = f"{ARMOURY_ROOT}/{name}"
        try:
            raw = self._props(path).GetAll(ARMOURY_IFACE, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            raise AsusdError(f"{name} is not available: {_dbus_message(exc)}") from exc
        return {str(k): _plain(v) for k, v in raw.items()}

    def _lookup_armoury(self, logical: str) -> tuple[str, dict[str, Any]] | None:
        if logical in self._attr_cache:
            cached = self._attr_cache[logical]
            if cached is None:
                return None
            candidates: tuple[str, ...] = (cached,)
        else:
            candidates = ATTRIBUTE_ALIASES.get(logical, (logical,))
        for candidate in candidates:
            try:
                data = self._armoury_get(candidate)
            except AsusdError:
                continue
            self._attr_cache[logical] = candidate
            return candidate, data
        self._attr_cache[logical] = None
        return None

    def armoury(self, name: str) -> dict[str, Any]:
        found = self._lookup_armoury(name)
        if found is None:
            raise AsusdError(f"{name} is not available on this model")
        return found[1]

    def armoury_available(self, name: str) -> bool:
        return self._lookup_armoury(name) is not None

    def resolved_attributes(self) -> dict[str, str | None]:
        """Report which firmware attribute each logical name resolved to."""
        resolved: dict[str, str | None] = {}
        for logical in ATTRIBUTE_ALIASES:
            found = self._lookup_armoury(logical)
            resolved[logical] = found[0] if found else None
        return resolved

    def set_armoury(self, name: str, value: int) -> None:
        found = self._lookup_armoury(name)
        if found is None:
            raise AsusdError(f"{name} is not available on this model")
        resolved = found[0]
        path = f"{ARMOURY_ROOT}/{resolved}"
        try:
            self._props(path).Set(
                ARMOURY_IFACE,
                "CurrentValue",
                dbus.Int32(int(value), variant_level=1),
                timeout=TIMEOUT,
            )
        except dbus.DBusException as exc:
            raise AsusdError(f"Could not set {resolved}: {_dbus_message(exc)}") from exc

    def apply_queued_gpu(self) -> bool:
        for logical in (ATTR_DGPU_DISABLE, ATTR_GPU_MUX):
            found = self._lookup_armoury(logical)
            if found is None:
                continue
            resolved = found[0]
            try:
                result = self._iface(
                    f"{ARMOURY_ROOT}/{resolved}", ARMOURY_IFACE
                ).ApplyQueuedGpuValue(timeout=TIMEOUT)
                return bool(result)
            except dbus.DBusException as exc:
                raise AsusdError(f"Could not queue GPU change: {_dbus_message(exc)}") from exc
        raise AsusdError("This machine has no queued GPU switch to apply.")

    def pending_reboot(self) -> bool:
        try:
            return bool(int(self.armoury(ATTR_PENDING_REBOOT).get("CurrentValue", 0)))
        except AsusdError:
            return False

    # -- misc -----------------------------------------------------------
    def _call(self, what: str, fn) -> Any:
        try:
            return fn()
        except dbus.DBusException as exc:
            raise AsusdError(f"{what}: {_dbus_message(exc)}") from exc


def _as_variant(value: Any) -> Any:
    """Re-wrap a dbus scalar as a variant for Properties.Set."""
    if isinstance(value, dbus.Boolean):
        return dbus.Boolean(bool(value), variant_level=1)
    if isinstance(value, dbus.Byte):
        return dbus.Byte(int(value), variant_level=1)
    if isinstance(value, dbus.UInt32):
        return dbus.UInt32(int(value), variant_level=1)
    if isinstance(value, dbus.Int32):
        return dbus.Int32(int(value), variant_level=1)
    if isinstance(value, dbus.UInt64):
        return dbus.UInt64(int(value), variant_level=1)
    return value


def _dbus_message(exc: dbus.DBusException) -> str:
    try:
        message = exc.get_dbus_message()
    except Exception:  # pragma: no cover - defensive
        message = ""
    return message or exc.get_dbus_name() or str(exc)


def _plain(value: Any) -> Any:
    """Convert dbus types to plain Python values."""
    if isinstance(value, (dbus.Boolean,)):
        return bool(value)
    if isinstance(value, (dbus.Byte, dbus.Int16, dbus.UInt16, dbus.Int32, dbus.UInt32,
                          dbus.Int64, dbus.UInt64, dbus.Double)):
        return value.real if hasattr(value, "real") else value
    if isinstance(value, (dbus.Array, list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return str(value)
