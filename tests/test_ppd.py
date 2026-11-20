# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for PowerProfiles (tuned-ppd / PPD) and UPower D-Bus helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import dbus

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import asusd as asusd_mod  # noqa: E402
from ghelper.backend import ppd  # noqa: E402


class FakeBus:
    def __init__(self, owners: set[str], props_iface: mock.Mock | None = None) -> None:
        self.owners = owners
        self.props_iface = props_iface or mock.Mock()
        self.get_object = mock.Mock(return_value=mock.Mock())
        self.add_signal_receiver = mock.Mock(return_value="signal-match")

    def name_has_owner(self, name: str) -> bool:
        return name in self.owners


class PpdTests(unittest.TestCase):
    def test_profile_to_ppd_mapping(self) -> None:
        self.assertEqual(ppd.profile_to_ppd(asusd_mod.QUIET), "power-saver")
        self.assertEqual(ppd.profile_to_ppd(asusd_mod.LOW_POWER), "power-saver")
        self.assertEqual(ppd.profile_to_ppd(asusd_mod.BALANCED), "balanced")
        self.assertEqual(ppd.profile_to_ppd(asusd_mod.PERFORMANCE), "performance")
        self.assertIsNone(ppd.profile_to_ppd(asusd_mod.CUSTOM))

    def test_sync_profile_updates_when_different(self) -> None:
        props = mock.Mock()
        props.Get.return_value = "balanced"
        bus = FakeBus({"org.freedesktop.UPower.PowerProfiles"}, props)

        with mock.patch.object(ppd.dbus, "Interface", return_value=props):
            changed = ppd.sync_profile(asusd_mod.QUIET, bus=bus)

        self.assertTrue(changed)
        props.Get.assert_called_once_with(
            "org.freedesktop.UPower.PowerProfiles", "ActiveProfile", timeout=ppd.TIMEOUT
        )
        props.Set.assert_called_once()
        iface, prop_name, variant = props.Set.call_args.args
        self.assertEqual(iface, "org.freedesktop.UPower.PowerProfiles")
        self.assertEqual(prop_name, "ActiveProfile")
        self.assertIsInstance(variant, dbus.String)
        self.assertEqual(str(variant), "power-saver")
        self.assertEqual(variant.variant_level, 1)

    def test_sync_profile_skips_when_already_matching(self) -> None:
        props = mock.Mock()
        props.Get.return_value = "power-saver"
        bus = FakeBus({"org.freedesktop.UPower.PowerProfiles"}, props)

        with mock.patch.object(ppd.dbus, "Interface", return_value=props):
            changed = ppd.sync_profile(asusd_mod.QUIET, bus=bus)

        self.assertFalse(changed)
        props.Set.assert_not_called()

    def test_sync_profile_falls_back_to_legacy_bus_name(self) -> None:
        props = mock.Mock()
        props.Get.return_value = "balanced"
        bus = FakeBus({"net.hadess.PowerProfiles"}, props)

        with mock.patch.object(ppd.dbus, "Interface", return_value=props):
            changed = ppd.sync_profile(asusd_mod.PERFORMANCE, bus=bus)

        self.assertTrue(changed)
        bus.get_object.assert_called_once_with(
            "net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", introspect=False
        )
        self.assertEqual(props.Set.call_args.args[0], "net.hadess.PowerProfiles")
        self.assertEqual(str(props.Set.call_args.args[2]), "performance")

    def test_sync_profile_returns_false_for_absent_custom_and_dbus_error(self) -> None:
        self.assertFalse(ppd.sync_profile(asusd_mod.CUSTOM, bus=FakeBus(set())))
        self.assertFalse(ppd.sync_profile(asusd_mod.QUIET, bus=FakeBus(set())))

        props = mock.Mock()
        props.Get.return_value = "balanced"
        props.Set.side_effect = dbus.DBusException("denied")
        bus = FakeBus({"org.freedesktop.UPower.PowerProfiles"}, props)
        with mock.patch.object(ppd.dbus, "Interface", return_value=props):
            self.assertFalse(ppd.sync_profile(asusd_mod.QUIET, bus=bus))

    def test_state_and_active_profile(self) -> None:
        absent_bus = FakeBus(set())
        self.assertEqual(ppd.state(bus=absent_bus), {"available": False})
        self.assertIsNone(ppd.active_profile(bus=absent_bus))

        props = mock.Mock()
        props.Get.return_value = "balanced"
        props.GetAll.return_value = {
            "ActiveProfile": "balanced",
            "Profiles": [
                {"Profile": "power-saver", "Driver": "tuned"},
                {"Profile": "balanced", "Driver": "tuned"},
                {"Profile": "performance", "Driver": "tuned"},
            ],
        }
        bus = FakeBus({"org.freedesktop.UPower.PowerProfiles"}, props)
        with mock.patch.object(ppd.dbus, "Interface", return_value=props):
            self.assertEqual(ppd.active_profile(bus=bus), "balanced")
            self.assertEqual(
                ppd.state(bus=bus),
                {
                    "available": True,
                    "service": "org.freedesktop.UPower.PowerProfiles",
                    "active_profile": "balanced",
                    "driver": "tuned",
                    "profiles": ["power-saver", "balanced", "performance"],
                },
            )

    def test_watch_ac_online_filters_and_inverts_on_battery(self) -> None:
        bus = FakeBus(set())
        seen: list[bool] = []
        match = ppd.watch_ac_online(seen.append, bus=bus)
        self.assertEqual(match, "signal-match")

        handler = bus.add_signal_receiver.call_args.args[0]
        handler("org.freedesktop.UPower.Device", {"OnBattery": True}, [])
        handler("org.freedesktop.UPower", {"LidIsClosed": True}, [])
        handler("org.freedesktop.UPower", {"OnBattery": True}, [])
        handler("org.freedesktop.UPower", {"OnBattery": False}, [])
        self.assertEqual(seen, [False, True])


if __name__ == "__main__":
    unittest.main()
