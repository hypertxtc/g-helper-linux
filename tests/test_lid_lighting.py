# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for D-Bus discovery and Slash / AniMe Matrix lighting backends."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import dbus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import anime, dbus_introspect, slash  # noqa: E402


SLASH_XML = """
<node>
  <node name="slash"/>
  <node name="asus_armoury"/>
</node>
"""

SLASH_NODE_XML = """
<node>
  <interface name="xyz.ljones.Slash">
    <property name="Enabled" type="b" access="readwrite"/>
    <property name="Mode" type="y" access="readwrite"/>
    <property name="Brightness" type="y" access="readwrite"/>
    <property name="Interval" type="y" access="readwrite"/>
    <property name="ShowOnBoot" type="b" access="readwrite"/>
  </interface>
</node>
"""

ANIME_ROOT_XML = """
<node>
  <interface name="xyz.ljones.Anime">
    <property name="EnableDisplay" type="b" access="readwrite"/>
    <property name="Brightness" type="y" access="readwrite"/>
    <property name="BuiltinsEnabled" type="b" access="readwrite"/>
    <property name="BuiltinAnimations" type="(yyyy)" access="readwrite"/>
  </interface>
</node>
"""


class FakeBus:
    def __init__(self, xml_by_path: dict[str, str], *, owned: bool = True) -> None:
        self._xml = xml_by_path
        self._owned = owned
        self.visited: list[str] = []

    def name_has_owner(self, _name: str) -> bool:
        return self._owned

    def get_object(self, _bus_name: str, path: str, introspect: bool = False):
        return path

    def introspect(self, path: str) -> str:
        self.visited.append(path)
        if path not in self._xml:
            raise dbus.DBusException("unknown object")
        return self._xml[path]


class IntrospectTests(unittest.TestCase):
    def test_parse_introspect_extracts_properties_and_children(self) -> None:
        found, props, children = dbus_introspect.parse_introspect(
            SLASH_NODE_XML, "xyz.ljones.Slash"
        )
        self.assertTrue(found)
        self.assertEqual(props["Mode"], "y")
        self.assertEqual(props["Enabled"], "b")
        self.assertEqual(children, [])

        found2, props2, children2 = dbus_introspect.parse_introspect(
            SLASH_XML, "xyz.ljones.Slash"
        )
        self.assertFalse(found2)
        self.assertEqual(props2, {})
        self.assertEqual(children2, ["slash", "asus_armoury"])

    def test_discover_finds_child_node_and_skips_unrelated(self) -> None:
        bus = FakeBus({"/xyz/ljones": SLASH_XML, "/xyz/ljones/slash": SLASH_NODE_XML})
        with mock.patch.object(
            dbus_introspect.dbus,
            "Interface",
            side_effect=lambda path, _iface: mock.Mock(
                Introspect=lambda timeout=10.0: bus.introspect(path)
            ),
        ):
            found = dbus_introspect.discover_interface(
                bus,
                "xyz.ljones.Asusd",
                ("/xyz/ljones", "/xyz/ljones/slash"),
                "xyz.ljones.Slash",
            )
        self.assertEqual(
            found,
            (
                "/xyz/ljones/slash",
                {
                    "Enabled": "b",
                    "Mode": "y",
                    "Brightness": "y",
                    "Interval": "y",
                    "ShowOnBoot": "b",
                },
            ),
        )
        self.assertNotIn("/xyz/ljones/asus_armoury", bus.visited)

    def test_discover_returns_none_when_unowned_or_missing(self) -> None:
        unowned = FakeBus({}, owned=False)
        self.assertIsNone(
            dbus_introspect.discover_interface(
                unowned, "xyz.ljones.Asusd", ("/xyz/ljones",), "xyz.ljones.Slash"
            )
        )

        bus = FakeBus({"/xyz/ljones": "<node/>"})
        with mock.patch.object(
            dbus_introspect.dbus,
            "Interface",
            side_effect=lambda path, _iface: mock.Mock(
                Introspect=lambda timeout=10.0: bus.introspect(path)
            ),
        ):
            self.assertIsNone(
                dbus_introspect.discover_interface(
                    bus, "xyz.ljones.Asusd", ("/xyz/ljones",), "xyz.ljones.Anime"
                )
            )


class SlashTests(unittest.TestCase):
    def test_all_16_modes_round_trip(self) -> None:
        self.assertEqual(len(slash.MODES), 16)
        for code, name, label in slash.MODES:
            with self.subTest(mode=name):
                self.assertEqual(slash.parse_mode(code), code)
                self.assertEqual(slash.parse_mode(name), code)
                self.assertEqual(slash.parse_mode(name.lower()), code)
                self.assertEqual(slash.parse_mode(label.lower()), code)

    def test_encode_mode_matches_wire_signature(self) -> None:
        v_byte = slash.encode_mode(0x1D, "y")
        self.assertIsInstance(v_byte, dbus.Byte)
        self.assertEqual(int(v_byte), 0x1D)

        v_u32 = slash.encode_mode("Game Over", "u")
        self.assertIsInstance(v_u32, dbus.UInt32)
        self.assertEqual(int(v_u32), 0x42)

        v_str = slash.encode_mode(0x1D, "s")
        self.assertIsInstance(v_str, dbus.String)
        self.assertEqual(str(v_str), "BitStream")

    def test_state_and_setters(self) -> None:
        dev = slash.Slash()
        props = mock.Mock()
        props.GetAll.return_value = {
            "Enabled": dbus.Boolean(True),
            "Mode": dbus.Byte(0x1D),
            "Brightness": dbus.Byte(180),
            "Interval": dbus.Byte(2),
            "ShowOnBoot": dbus.Boolean(True),
            "ShowOnShutdown": dbus.Boolean(False),
            "ShowOnSleep": dbus.Boolean(True),
            "ShowOnBattery": dbus.Boolean(False),
            "ShowOnLidClosed": dbus.Boolean(False),
            "ShowBatteryWarning": dbus.Boolean(True),
        }
        dev._bus = mock.Mock()
        dev._path = "/xyz/ljones/slash"
        dev._prop_types = {"Mode": "y", "Brightness": "y", "Interval": "y"}
        dev._available = True

        with mock.patch.object(dev, "_props", return_value=props):
            state = dev.state()
            self.assertTrue(state["enabled"])
            self.assertEqual(state["mode"], 0x1D)
            self.assertEqual(state["mode_name"], "BitStream")
            self.assertEqual(state["brightness"], 180)
            self.assertEqual(state["interval"], 2)
            self.assertTrue(state["show_on_boot"])
            self.assertFalse(state["show_on_battery"])

            dev.set_brightness(999)
            self.assertEqual(int(props.Set.call_args.args[2]), 255)
            dev.set_interval(-3)
            self.assertEqual(int(props.Set.call_args.args[2]), 0)
            dev.set_option("ShowOnBoot", False)
            self.assertIsInstance(props.Set.call_args.args[2], dbus.Boolean)
            with self.assertRaises(slash.SlashError):
                dev.set_option("Bogus", True)

    def test_unavailable_when_bus_missing(self) -> None:
        dev = slash.Slash()
        with mock.patch.object(
            slash.dbus, "SystemBus", side_effect=dbus.DBusException("no bus")
        ):
            self.assertFalse(dev.available)


class AnimeTests(unittest.TestCase):
    def test_brightness_parse_and_encode(self) -> None:
        self.assertEqual(anime.parse_brightness(0), 0)
        self.assertEqual(anime.parse_brightness(3), 3)
        self.assertEqual(anime.parse_brightness("Med"), 2)
        self.assertEqual(anime.parse_brightness("medium"), 2)
        self.assertEqual(anime.parse_brightness(0.0), 0)
        self.assertEqual(anime.parse_brightness(1.0), 3)
        self.assertEqual(anime.parse_brightness(0.67), 2)

        self.assertIsInstance(anime.encode_brightness(2, "y"), dbus.Byte)
        self.assertIsInstance(anime.encode_brightness(2, "u"), dbus.UInt32)
        self.assertEqual(str(anime.encode_brightness(2, "s")), "Med")
        self.assertAlmostEqual(float(anime.encode_brightness(3, "d")), 1.0)

    def test_builtin_animations_parse_and_encode(self) -> None:
        parsed = anime.parse_builtin_animations(
            ("StaticEmergence", 1, "BannerSwipe", "SeeYa")
        )
        self.assertEqual(
            parsed, {"boot": 1, "awake": 1, "sleep": 0, "shutdown": 1}
        )

        bytes_struct = anime.encode_builtin_animations(1, 0, 1, 0, "(yyyy)")
        self.assertEqual([int(v) for v in bytes_struct], [1, 0, 1, 0])
        self.assertIsInstance(bytes_struct[0], dbus.Byte)

        u32_struct = anime.encode_builtin_animations(0, 1, 0, 1, "(uuuu)")
        self.assertIsInstance(u32_struct[0], dbus.UInt32)

        str_struct = anime.encode_builtin_animations(0, 1, 1, 0, "(ssss)")
        self.assertEqual(
            [str(v) for v in str_struct],
            ["GlitchConstruction", "RogLogoGlitch", "Starfield", "GlitchOut"],
        )

    def test_state_and_setters(self) -> None:
        dev = anime.Anime()
        props = mock.Mock()
        props.GetAll.return_value = {
            "EnableDisplay": dbus.Boolean(True),
            "Brightness": dbus.Byte(2),
            "BuiltinsEnabled": dbus.Boolean(True),
            "BuiltinAnimations": (
                dbus.Byte(1),
                dbus.Byte(0),
                dbus.Byte(1),
                dbus.Byte(1),
            ),
            "OffWhenUnplugged": dbus.Boolean(True),
            "OffWhenSuspended": dbus.Boolean(False),
            "OffWhenLidClosed": dbus.Boolean(True),
        }
        dev._bus = mock.Mock()
        dev._path = "/xyz/ljones"
        dev._prop_types = {"Brightness": "y", "BuiltinAnimations": "(yyyy)"}
        dev._available = True

        with mock.patch.object(dev, "_props", return_value=props):
            state = dev.state()
            self.assertTrue(state["enabled"])
            self.assertEqual(state["brightness"], 2)
            self.assertEqual(state["brightness_name"], "Medium")
            self.assertTrue(state["builtins_enabled"])
            self.assertEqual(
                state["builtin_animations"],
                {"boot": 1, "awake": 0, "sleep": 1, "shutdown": 1},
            )
            self.assertTrue(state["off_when_unplugged"])

            dev.set_builtin_animations(1, 1, 0, 0)
            self.assertEqual([int(v) for v in props.Set.call_args.args[2]], [1, 1, 0, 0])
            dev.set_option("OffWhenLidClosed", False)
            self.assertIsInstance(props.Set.call_args.args[2], dbus.Boolean)
            with self.assertRaises(anime.AnimeError):
                dev.set_option("InvalidProp", True)


if __name__ == "__main__":
    unittest.main()
