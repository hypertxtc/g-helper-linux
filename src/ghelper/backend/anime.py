# SPDX-License-Identifier: GPL-3.0-or-later

"""AniMe Matrix lid display control through asusd's xyz.ljones.Anime interface."""

from __future__ import annotations

import logging
from typing import Any

import dbus

from .dbus_introspect import discover_interface, infer_dbus_signature

log = logging.getLogger(__name__)

BUS_NAME = "xyz.ljones.Asusd"
ANIME_IFACE = "xyz.ljones.Anime"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
CANDIDATE_PATHS = ("/xyz/ljones", "/xyz/ljones/anime")
TIMEOUT = 10.0

#: (level, asusd enum name, display label)
BRIGHTNESS_LEVELS: tuple[tuple[int, str, str], ...] = (
    (0, "Off", "Off"),
    (1, "Low", "Low"),
    (2, "Med", "Medium"),
    (3, "High", "High"),
)
BRIGHTNESS_NAME_BY_LEVEL: dict[int, str] = {lvl: name for lvl, name, _lbl in BRIGHTNESS_LEVELS}
BRIGHTNESS_LABEL_BY_LEVEL: dict[int, str] = {lvl: lbl for lvl, _name, lbl in BRIGHTNESS_LEVELS}
_BRIGHTNESS_BY_TEXT: dict[str, int] = {
    "off": 0,
    "low": 1,
    "med": 2,
    "medium": 2,
    "high": 3,
}

BOOT_ANIMS: tuple[tuple[int, str, str], ...] = (
    (0, "GlitchConstruction", "Glitch Construction"),
    (1, "StaticEmergence", "Static Emergence"),
)
AWAKE_ANIMS: tuple[tuple[int, str, str], ...] = (
    (0, "BinaryBannerScroll", "Binary Banner Scroll"),
    (1, "RogLogoGlitch", "ROG Logo Glitch"),
)
SLEEP_ANIMS: tuple[tuple[int, str, str], ...] = (
    (0, "BannerSwipe", "Banner Swipe"),
    (1, "Starfield", "Starfield"),
)
SHUTDOWN_ANIMS: tuple[tuple[int, str, str], ...] = (
    (0, "GlitchOut", "Glitch Out"),
    (1, "SeeYa", "See Ya"),
)

BUILTIN_STAGES: tuple[tuple[str, str, tuple[tuple[int, str, str], ...]], ...] = (
    ("boot", "Boot", BOOT_ANIMS),
    ("awake", "Awake", AWAKE_ANIMS),
    ("sleep", "Sleep", SLEEP_ANIMS),
    ("shutdown", "Shutdown", SHUTDOWN_ANIMS),
)
STAGE_BY_KEY: dict[str, tuple[tuple[int, str, str], ...]] = {
    key: options for key, _label, options in BUILTIN_STAGES
}
STAGE_ORDER: tuple[str, ...] = ("boot", "awake", "sleep", "shutdown")

OPTION_PROPERTIES = (
    "OffWhenUnplugged",
    "OffWhenSuspended",
    "OffWhenLidClosed",
)


class AnimeError(RuntimeError):
    pass


def parse_brightness(value: Any) -> int:
    """Normalise an integer, float (0.0-1.0) or string brightness into 0..3."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) or isinstance(value, dbus.Double):
        ratio = max(0.0, min(1.0, float(value)))
        return max(0, min(3, round(ratio * 3)))
    if isinstance(value, int):
        return max(0, min(3, int(value)))
    text = str(value).strip().lower()
    if text in _BRIGHTNESS_BY_TEXT:
        return _BRIGHTNESS_BY_TEXT[text]
    try:
        return max(0, min(3, int(text)))
    except ValueError:
        try:
            ratio = max(0.0, min(1.0, float(text)))
            return max(0, min(3, round(ratio * 3)))
        except ValueError:
            return 0


def encode_brightness(level: int, signature: str = "y"):
    """Encode brightness level 0..3 as a variant matching ``signature``."""
    clamped = max(0, min(3, int(level)))
    if signature == "d":
        return dbus.Double(clamped / 3.0, variant_level=1)
    if signature == "s":
        return dbus.String(BRIGHTNESS_NAME_BY_LEVEL[clamped], variant_level=1)
    if signature == "u":
        return dbus.UInt32(clamped, variant_level=1)
    if signature == "i":
        return dbus.Int32(clamped, variant_level=1)
    return dbus.Byte(clamped, variant_level=1)


def parse_stage_anim(stage: str, value: Any) -> int:
    """Convert an integer or variant name for ``stage`` into its option index."""
    options = STAGE_BY_KEY.get(stage, ())
    if not options:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        idx = int(value)
        return idx if 0 <= idx < len(options) else 0
    text = str(value).strip()
    norm = text.lower().replace(" ", "").replace("-", "").replace("_", "")
    for idx, name, label in options:
        if norm in (name.lower(), label.lower().replace(" ", "")):
            return idx
    try:
        idx = int(text)
        return idx if 0 <= idx < len(options) else 0
    except ValueError:
        return 0


def parse_builtin_animations(raw: Any) -> dict[str, int]:
    """Parse ``BuiltinAnimations`` tuple/list into stage -> index mapping."""
    result = {stage: 0 for stage in STAGE_ORDER}
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        for index, stage in enumerate(STAGE_ORDER):
            result[stage] = parse_stage_anim(stage, raw[index])
    return result


def encode_builtin_animations(
    boot: int, awake: int, sleep: int, shutdown: int, signature: str = "(yyyy)"
):
    """Encode the 4-stage ``BuiltinAnimations`` struct matching ``signature``."""
    raw_values = (boot, awake, sleep, shutdown)
    values = tuple(
        max(0, min(len(STAGE_BY_KEY[stage]) - 1, int(val)))
        for stage, val in zip(STAGE_ORDER, raw_values)
    )
    if signature == "(ssss)":
        names = tuple(
            STAGE_BY_KEY[stage][val][1]
            for stage, val in zip(STAGE_ORDER, values)
        )
        return dbus.Struct(
            tuple(dbus.String(name) for name in names),
            signature="ssss",
            variant_level=1,
        )
    if signature == "(uuuu)":
        return dbus.Struct(
            tuple(dbus.UInt32(val) for val in values),
            signature="uuuu",
            variant_level=1,
        )
    if signature == "(iiii)":
        return dbus.Struct(
            tuple(dbus.Int32(val) for val in values),
            signature="iiii",
            variant_level=1,
        )
    return dbus.Struct(
        tuple(dbus.Byte(val) for val in values),
        signature="yyyy",
        variant_level=1,
    )


class Anime:
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
                raise AnimeError(f"AniMe Matrix not available: {exc}") from exc
        return self._bus

    def _discover(self) -> None:
        found = discover_interface(
            self._get_bus(), BUS_NAME, CANDIDATE_PATHS, ANIME_IFACE, TIMEOUT
        )
        if found is None:
            self._available = False
            raise AnimeError("no AniMe Matrix found")
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
            except AnimeError:
                pass
        return bool(self._available)

    def state(self) -> dict[str, Any]:
        try:
            raw = self._props().GetAll(ANIME_IFACE, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            self._available = False
            raise AnimeError(str(exc)) from exc
        self._record_wire_types(raw)
        brightness = parse_brightness(raw.get("Brightness", 0))
        return {
            "enabled": bool(raw.get("EnableDisplay", False)),
            "brightness": brightness,
            "brightness_name": BRIGHTNESS_LABEL_BY_LEVEL.get(brightness, "Off"),
            "builtins_enabled": bool(raw.get("BuiltinsEnabled", False)),
            "builtin_animations": parse_builtin_animations(raw.get("BuiltinAnimations")),
            "off_when_unplugged": bool(raw.get("OffWhenUnplugged", False)),
            "off_when_suspended": bool(raw.get("OffWhenSuspended", False)),
            "off_when_lid_closed": bool(raw.get("OffWhenLidClosed", False)),
        }

    def _set_prop(self, name: str, value: Any, what: str) -> None:
        try:
            self._props().Set(ANIME_IFACE, name, value, timeout=TIMEOUT)
        except dbus.DBusException as exc:
            raise AnimeError(f"could not set {what}: {exc}") from exc

    def set_enabled(self, enabled: bool) -> None:
        self._props()
        self._set_prop(
            "EnableDisplay", dbus.Boolean(bool(enabled), variant_level=1), "AniMe Matrix state"
        )

    def set_brightness(self, level: int) -> None:
        self._props()
        sig = self._prop_types.get("Brightness", "y")
        self._set_prop("Brightness", encode_brightness(level, sig), "AniMe brightness")

    def set_builtins_enabled(self, enabled: bool) -> None:
        self._props()
        self._set_prop(
            "BuiltinsEnabled",
            dbus.Boolean(bool(enabled), variant_level=1),
            "AniMe built-in animations",
        )

    def set_builtin_animations(
        self, boot: int, awake: int, sleep: int, shutdown: int
    ) -> None:
        self._props()
        sig = self._prop_types.get("BuiltinAnimations", "(yyyy)")
        self._set_prop(
            "BuiltinAnimations",
            encode_builtin_animations(boot, awake, sleep, shutdown, sig),
            "AniMe built-in presets",
        )

    def set_option(self, prop_name: str, enabled: bool) -> None:
        if prop_name not in OPTION_PROPERTIES:
            raise AnimeError(f"unknown AniMe option {prop_name!r}")
        self._props()
        self._set_prop(prop_name, dbus.Boolean(bool(enabled), variant_level=1), prop_name)
