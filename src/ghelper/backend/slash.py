# SPDX-License-Identifier: GPL-3.0-or-later

"""ROG Slash LED bar control through asusd's xyz.ljones.Slash interface."""

from __future__ import annotations

import logging
from typing import Any

import dbus

from .dbus_introspect import discover_interface, infer_dbus_signature

log = logging.getLogger(__name__)

BUS_NAME = "xyz.ljones.Asusd"
SLASH_IFACE = "xyz.ljones.Slash"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
CANDIDATE_PATHS = ("/xyz/ljones", "/xyz/ljones/slash")
TIMEOUT = 10.0

#: (discriminant, asusd enum name, display label)
MODES: tuple[tuple[int, str, str], ...] = (
    (0x06, "Static", "Static"),
    (0x10, "Bounce", "Bounce"),
    (0x12, "Slash", "Slash"),
    (0x13, "Loading", "Loading"),
    (0x1D, "BitStream", "BitStream"),
    (0x1A, "Transmission", "Transmission"),
    (0x19, "Flow", "Flow"),
    (0x25, "Flux", "Flux"),
    (0x24, "Phantom", "Phantom"),
    (0x26, "Spectrum", "Spectrum"),
    (0x32, "Hazard", "Hazard"),
    (0x33, "Interfacing", "Interfacing"),
    (0x34, "Ramp", "Ramp"),
    (0x42, "GameOver", "Game Over"),
    (0x43, "Start", "Start"),
    (0x44, "Buzzer", "Buzzer"),
)

MODE_IDS: tuple[int, ...] = tuple(code for code, _name, _label in MODES)
MODE_BY_ID: dict[int, str] = {code: name for code, name, _label in MODES}
MODE_LABELS: dict[int, str] = {code: label for code, _name, label in MODES}
MODE_ID_BY_NAME: dict[str, int] = {}
for _code, _name, _label in MODES:
    MODE_ID_BY_NAME[_name.lower()] = _code
    MODE_ID_BY_NAME[_label.lower().replace(" ", "")] = _code

DEFAULT_MODE = 0x10  # Bounce

OPTION_PROPERTIES = (
    "ShowOnBoot",
    "ShowOnShutdown",
    "ShowOnSleep",
    "ShowOnBattery",
    "ShowOnLidClosed",
    "ShowBatteryWarning",
)


class SlashError(RuntimeError):
    pass


def parse_mode(value: Any) -> int:
    """Convert an integer discriminant or mode name into a SlashMode id."""
    if isinstance(value, bool):
        return DEFAULT_MODE
    if isinstance(value, int):
        return int(value)
    text = str(value).strip()
    norm = text.lower().replace(" ", "").replace("-", "").replace("_", "")
    if norm in MODE_ID_BY_NAME:
        return MODE_ID_BY_NAME[norm]
    try:
        return int(text, 0)
    except ValueError:
        return DEFAULT_MODE


def encode_mode(mode: int | str, signature: str = "y"):
    """Encode ``mode`` as a variant matching the introspected D-Bus signature."""
    code = parse_mode(mode)
    if signature == "s":
        return dbus.String(MODE_BY_ID.get(code, "Bounce"), variant_level=1)
    if signature == "u":
        return dbus.UInt32(code, variant_level=1)
    if signature == "i":
        return dbus.Int32(code, variant_level=1)
    return dbus.Byte(code, variant_level=1)


def encode_byte(value: int, low: int, high: int, signature: str = "y"):
    """Clamp ``value`` to ``low..high`` and wrap as a D-Bus variant."""
    clamped = max(low, min(high, int(value)))
    if signature == "u":
        return dbus.UInt32(clamped, variant_level=1)
    if signature == "i":
        return dbus.Int32(clamped, variant_level=1)
    return dbus.Byte(clamped, variant_level=1)


class Slash:
    def __init__(self) -> None:
        self._bus: dbus.SystemBus | None = None
        self._path: str | None = None
        self._prop_types: dict[str, str] = {}
        self._available: bool | None = None

    def _get_bus(self) -> dbus.SystemBus:
        if self._bus is None:
            try:
                self._bus = dbus.SystemBus()
            except dbus.DBusException as exc:
                self._available = False
                raise SlashError(f"Slash lighting not available: {exc}") from exc
        return self._bus

    def _discover(self) -> None:
        found = discover_interface(
            self._get_bus(), BUS_NAME, CANDIDATE_PATHS, SLASH_IFACE, TIMEOUT
        )
        if found is None:
            self._available = False
            raise SlashError("no Slash lighting found")
        self._path, self._prop_types = found
        self._available = True

    def _props(self):
        if self._path is None:
            self._discover()
        assert self._path is not None
        obj = self._get_bus().get_object(BUS_NAME, self._path, introspect=False)
        return dbus.Interface(obj, PROPS_IFACE)

    def _record_wire_types(self, raw: dict[Any, Any]) -> None:
        for key, value in raw.items():
            name = str(key)
            if name not in self._prop_types:
                sig = infer_dbus_signature(value)
                if sig is not None:
                    self._prop_types[name] = sig

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                self._discover()
            except SlashError:
                pass
        return bool(self._available)

    def state(self) -> dict[str, Any]:
        try:
            raw = self._props().GetAll(SLASH_IFACE, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            self._available = False
            raise SlashError(str(exc)) from exc
        self._record_wire_types(raw)
        mode = parse_mode(raw.get("Mode", DEFAULT_MODE))
        return {
            "enabled": bool(raw.get("Enabled", False)),
            "mode": mode,
            "mode_name": MODE_LABELS.get(mode, MODE_BY_ID.get(mode, f"Mode {mode}")),
            "brightness": int(raw.get("Brightness", 0)),
            "interval": int(raw.get("Interval", 0)),
            "show_on_boot": bool(raw.get("ShowOnBoot", False)),
            "show_on_shutdown": bool(raw.get("ShowOnShutdown", False)),
            "show_on_sleep": bool(raw.get("ShowOnSleep", False)),
            "show_on_battery": bool(raw.get("ShowOnBattery", False)),
            "show_on_lid_closed": bool(raw.get("ShowOnLidClosed", False)),
            "show_battery_warning": bool(raw.get("ShowBatteryWarning", False)),
        }

    def _set_prop(self, name: str, value: Any, what: str) -> None:
        try:
            self._props().Set(SLASH_IFACE, name, value, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            raise SlashError(f"could not set {what}: {exc}") from exc

    def set_enabled(self, enabled: bool) -> None:
        self._props()
        self._set_prop("Enabled", dbus.Boolean(bool(enabled), variant_level=1), "Slash state")

    def set_mode(self, mode: int | str) -> None:
        self._props()
        sig = self._prop_types.get("Mode", "y")
        self._set_prop("Mode", encode_mode(mode, sig), "Slash mode")

    def set_brightness(self, value: int) -> None:
        self._props()
        sig = self._prop_types.get("Brightness", "y")
        self._set_prop("Brightness", encode_byte(value, 0, 255, sig), "Slash brightness")

    def set_interval(self, value: int) -> None:
        self._props()
        sig = self._prop_types.get("Interval", "y")
        self._set_prop("Interval", encode_byte(value, 0, 5, sig), "Slash interval")

    def set_option(self, prop_name: str, enabled: bool) -> None:
        if prop_name not in OPTION_PROPERTIES:
            raise SlashError(f"unknown Slash option {prop_name!r}")
        self._props()
        self._set_prop(prop_name, dbus.Boolean(bool(enabled), variant_level=1), prop_name)
