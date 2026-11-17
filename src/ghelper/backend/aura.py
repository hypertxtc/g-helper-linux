# SPDX-License-Identifier: GPL-3.0-or-later

"""Keyboard Aura control through asusd's xyz.ljones.Aura interface."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import dbus

log = logging.getLogger(__name__)

BUS_NAME = "xyz.ljones.Asusd"
AURA_ROOT = "/xyz/ljones/aura"
AURA_IFACE = "xyz.ljones.Aura"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
TIMEOUT = 10.0

MODE_NAMES = {
    0: "Static",
    1: "Breathe",
    2: "Rainbow",
    3: "Rainbow wave",
    4: "Stars",
    5: "Rain",
    6: "Highlight",
    7: "Laser",
    8: "Ripple",
    10: "Pulse",
    11: "Comet",
    12: "Flash",
}

BRIGHTNESS_NAMES = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}

#: asusd's speed names, as stored in ``LedModeData``.
SPEED_NAMES = ("Low", "Med", "High")
SPEED_LABELS = {"Low": "Slow", "Med": "Medium", "High": "Fast"}


class AuraError(RuntimeError):
    pass


class Aura:
    def __init__(self) -> None:
        self._bus = dbus.SystemBus()
        self._path: str | None = None
        self._available: bool | None = None

    def _object(self, path: str):
        return self._bus.get_object(BUS_NAME, path, introspect=False)

    def _props(self):
        if self._path is None:
            self._discover()
        assert self._path is not None
        return dbus.Interface(self._object(self._path), PROPS_IFACE)

    def _discover(self) -> None:
        try:
            root = dbus.Interface(self._object(AURA_ROOT), "org.freedesktop.DBus.Introspectable")
            xml = root.Introspect(timeout=TIMEOUT)
            parsed = ET.fromstring(xml)
            names = [
                node.get("name")
                for node in parsed.findall("node")
                if node.get("name")
            ]
            if not names:
                raise AuraError("no Aura keyboard found")
            self._path = f"{AURA_ROOT}/{names[0]}"
            self._available = True
        except (dbus.DBusException, ET.ParseError) as exc:
            self._available = False
            raise AuraError(f"keyboard lighting not available: {exc}") from exc

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                self._discover()
            except AuraError:
                pass
        return bool(self._available)

    def state(self) -> dict[str, Any]:
        try:
            raw = self._props().GetAll(AURA_IFACE, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            self._available = False
            raise AuraError(str(exc)) from exc
        data = raw.get("LedModeData")
        mode = int(raw.get("LedMode", 0))
        color = (255, 255, 255)
        zone = 0
        speed = "Med"
        direction = "Right"
        if data is not None and len(data) >= 6:
            mode = int(data[0])
            # LedModeData is (mode, zone, colour1, colour2, speed, direction);
            # speed and direction are strings, not numbers.
            zone = int(data[1])
            color = tuple(int(v) for v in data[2])
            speed = str(data[4])
            direction = str(data[5])
        return {
            "mode": mode,
            "mode_name": MODE_NAMES.get(mode, f"Mode {mode}"),
            "brightness": int(raw.get("Brightness", 0)),
            "supported_modes": [int(v) for v in raw.get("SupportedBasicModes", [])],
            "supported_brightness": [int(v) for v in raw.get("SupportedBrightness", [])],
            "color": color,
            "zone": zone,
            "speed": speed,
            "direction": direction,
        }

    def set_brightness(self, value: int) -> None:
        try:
            self._props().Set(
                AURA_IFACE,
                "Brightness",
                dbus.UInt32(int(value), variant_level=1),
                timeout=TIMEOUT,
            )
        except dbus.DBusException as exc:
            raise AuraError(f"could not set keyboard brightness: {exc}") from exc

    def set_mode(self, mode: int) -> None:
        try:
            self._props().Set(
                AURA_IFACE,
                "LedMode",
                dbus.UInt32(int(mode), variant_level=1),
                timeout=TIMEOUT,
            )
        except dbus.DBusException as exc:
            raise AuraError(f"could not set keyboard mode: {exc}") from exc

    def _write_data(
        self,
        mode: int,
        zone: int,
        color: tuple[int, int, int],
        color2: tuple[int, int, int],
        speed: str,
        direction: str,
    ) -> None:
        red, green, blue = (max(0, min(255, int(v))) for v in color)
        payload = dbus.Struct(
            (
                dbus.UInt32(int(mode)),
                dbus.UInt32(int(zone)),
                dbus.Struct(
                    (dbus.Byte(red), dbus.Byte(green), dbus.Byte(blue)),
                    signature="yyy",
                ),
                dbus.Struct(
                    (dbus.Byte(color2[0]), dbus.Byte(color2[1]), dbus.Byte(color2[2])),
                    signature="yyy",
                ),
                dbus.String(str(speed)),
                dbus.String(str(direction)),
            ),
            signature="uu(yyy)(yyy)ss",
            variant_level=1,
        )
        try:
            self._props().Set(AURA_IFACE, "LedModeData", payload, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            raise AuraError(f"could not set keyboard lighting: {exc}") from exc

    def _data(self) -> tuple[int, int, tuple, tuple, str, str]:
        try:
            raw = self._props().GetAll(AURA_IFACE, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            raise AuraError(str(exc)) from exc
        data = raw.get("LedModeData")
        if data is None or len(data) < 6:
            raise AuraError("keyboard lighting data not available")
        return (
            int(data[0]),
            int(data[1]),
            tuple(int(v) for v in data[2]),
            tuple(int(v) for v in data[3]),
            str(data[4]),
            str(data[5]),
        )

    def set_color(self, color: tuple[int, int, int]) -> None:
        mode, zone, _color, color2, speed, direction = self._data()
        self._write_data(mode, zone, color, color2, speed, direction)

    def set_speed(self, value: str | int) -> None:
        """Animation speed: ``Low`` / ``Med`` / ``High`` (or 0 / 1 / 2)."""
        if isinstance(value, int):
            index = max(0, min(len(SPEED_NAMES) - 1, value))
            speed = SPEED_NAMES[index]
        else:
            speed = str(value).strip().capitalize()
            if speed not in SPEED_NAMES:
                raise AuraError(f"unknown keyboard speed {value!r}")
        mode, zone, color, color2, _speed, direction = self._data()
        self._write_data(mode, zone, color, color2, speed, direction)
