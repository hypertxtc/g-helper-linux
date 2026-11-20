# SPDX-License-Identifier: GPL-3.0-or-later

"""Tray menu layout tests that do not need a session bus or a running tray."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper import tray as tray_mod  # noqa: E402
from ghelper.backend import asusd  # noqa: E402


def make_menu(gpu_state: str | None, profile: int = asusd.BALANCED):
    # Bypass dbus.service.Object.__init__: _layout only reads the tray fields.
    menu = tray_mod._TrayMenu.__new__(tray_mod._TrayMenu)
    menu._tray = SimpleNamespace(
        profile=profile,
        gpu_state=gpu_state,
        select_gpu=lambda mode: None,
        select_profile=lambda value: None,
        show=lambda: None,
        quit=lambda: None,
    )
    return menu


def items(menu) -> dict[int, dict]:
    root = menu._layout()[1]
    return {int(child[0]): child[1] for child in root[2]}


class LayoutTests(unittest.TestCase):
    def test_menu_starts_with_profiles_and_ends_with_quit(self) -> None:
        menu = make_menu("standard")
        root = menu._layout()[1]
        labels = [
            str(child[1].get("label"))
            for child in root[2]
            if "label" in child[1]
        ]
        self.assertEqual(
            labels,
            [
                "Open G-Helper",
                "Silent",
                "Balanced",
                "Turbo",
                "Eco",
                "Standard",
                "Smart",
                "Optimized",
                "Quit",
            ],
        )

    def test_profile_radio_follows_the_active_profile(self) -> None:
        menu = make_menu("standard", profile=asusd.PERFORMANCE)
        menu_items = items(menu)
        self.assertEqual(
            int(menu_items[tray_mod.ITEM_PERFORMANCE]["toggle-state"]), 1
        )
        self.assertEqual(int(menu_items[tray_mod.ITEM_BALANCED]["toggle-state"]), 0)

        low_menu = make_menu("standard", profile=asusd.LOW_POWER)
        low_items = items(low_menu)
        self.assertEqual(int(low_items[tray_mod.ITEM_QUIET]["toggle-state"]), 1)
        self.assertEqual(int(low_items[tray_mod.ITEM_BALANCED]["toggle-state"]), 0)

    def test_gpu_radio_follows_cardwire(self) -> None:
        for state, active in (
            ("eco", tray_mod.ITEM_ECO),
            ("standard", tray_mod.ITEM_STANDARD),
            ("smart", tray_mod.ITEM_SMART),
            ("optimized", tray_mod.ITEM_OPTIMIZED),
        ):
            with self.subTest(state=state):
                menu_items = items(make_menu(state))
                for item in (
                    tray_mod.ITEM_ECO,
                    tray_mod.ITEM_STANDARD,
                    tray_mod.ITEM_SMART,
                    tray_mod.ITEM_OPTIMIZED,
                ):
                    self.assertEqual(
                        int(menu_items[item]["toggle-state"]),
                        1 if item == active else 0,
                    )

    def test_ultimate_and_manual_leave_every_radio_off(self) -> None:
        for state in (None, "ultimate", "manual"):
            with self.subTest(state=state):
                menu_items = items(make_menu(state))
                states = [
                    int(menu_items[item]["toggle-state"])
                    for item in (
                        tray_mod.ITEM_ECO,
                        tray_mod.ITEM_STANDARD,
                        tray_mod.ITEM_SMART,
                        tray_mod.ITEM_OPTIMIZED,
                    )
                ]
                self.assertEqual(states, [0, 0, 0, 0])

    def test_profile_and_gpu_radios_use_separate_groups(self) -> None:
        menu_items = items(make_menu("standard"))
        for item in (
            tray_mod.ITEM_QUIET,
            tray_mod.ITEM_BALANCED,
            tray_mod.ITEM_PERFORMANCE,
        ):
            self.assertEqual(int(menu_items[item]["toggle-group"]), 1)
        for item in (
            tray_mod.ITEM_ECO,
            tray_mod.ITEM_STANDARD,
            tray_mod.ITEM_SMART,
            tray_mod.ITEM_OPTIMIZED,
        ):
            self.assertEqual(int(menu_items[item]["toggle-group"]), 2)


class EventTests(unittest.TestCase):
    def test_gpu_clicks_reach_the_window(self) -> None:
        menu = make_menu("standard")
        chosen: list[str] = []
        menu._tray.select_gpu = chosen.append
        for item, mode in (
            (tray_mod.ITEM_ECO, "eco"),
            (tray_mod.ITEM_STANDARD, "standard"),
            (tray_mod.ITEM_SMART, "smart"),
            (tray_mod.ITEM_OPTIMIZED, "optimized"),
        ):
            menu.Event(item, "clicked", None, 0)
        self.assertEqual(chosen, ["eco", "standard", "smart", "optimized"])

    def test_profile_clicks_still_work(self) -> None:
        menu = make_menu("standard")
        chosen: list[int] = []
        menu._tray.select_profile = chosen.append
        menu.Event(tray_mod.ITEM_PERFORMANCE, "clicked", None, 0)
        self.assertEqual(chosen, [asusd.PERFORMANCE])

    def test_non_click_events_are_ignored(self) -> None:
        menu = make_menu("standard")
        chosen: list[str] = []
        menu._tray.select_gpu = chosen.append
        menu.Event(tray_mod.ITEM_SMART, "hovered", None, 0)
        self.assertEqual(chosen, [])

    def test_revision_increments_on_profile_and_gpu_changes(self) -> None:
        tray = tray_mod.Tray.__new__(tray_mod.Tray)
        tray.profile = asusd.BALANCED
        tray.gpu_state = "standard"
        tray.revision = 0
        tray.tooltip_title = "G-Helper"
        tray.tooltip_body = ""
        tray._icon = mock.Mock()
        menu = tray_mod._TrayMenu.__new__(tray_mod._TrayMenu)
        menu._tray = tray
        menu.LayoutUpdated = mock.Mock()
        tray._menu = menu

        self.assertEqual(int(menu._layout()[0]), 0)
        tray.set_profile(asusd.QUIET)
        self.assertEqual(tray.revision, 1)
        menu.LayoutUpdated.assert_called_with(1, 0)
        self.assertEqual(int(menu._layout()[0]), 1)

        tray.set_gpu_state("eco")
        self.assertEqual(tray.revision, 2)
        menu.LayoutUpdated.assert_called_with(2, 0)
        self.assertEqual(int(menu._layout()[0]), 2)


if __name__ == "__main__":
    unittest.main()
