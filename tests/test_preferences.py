# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for CLI argument forwarding and UI preference storage."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.arguments import parse_gui_arguments  # noqa: E402
from ghelper.backend import settings as settings_mod  # noqa: E402


class GuiArgumentsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        self.assertEqual(parse_gui_arguments(["ghelper-linux"]), ("main", False))

    def test_page_forms(self) -> None:
        self.assertEqual(
            parse_gui_arguments(["ghelper-linux", "--page", "fans"]), ("fans", False)
        )
        self.assertEqual(
            parse_gui_arguments(["ghelper-linux", "--page=settings"]),
            ("settings", False),
        )
        self.assertEqual(
            parse_gui_arguments(["ghelper-linux", "--page", "hotkeys"]),
            ("hotkeys", False),
        )

    def test_hidden_flags(self) -> None:
        for flag in ("--minimized", "--hidden", "-m"):
            self.assertEqual(
                parse_gui_arguments(["ghelper-linux", flag]), ("main", True)
            )

    def test_invalid_page_is_ignored(self) -> None:
        self.assertEqual(
            parse_gui_arguments(["ghelper-linux", "--page", "nope"]), ("main", False)
        )


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(os.environ.pop, "XDG_CONFIG_HOME", None)
        os.environ["XDG_CONFIG_HOME"] = self._dir.name

    def test_geometry_round_trip(self) -> None:
        store = settings_mod.Settings()
        self.assertEqual(store.get("window_width"), 430)
        store.set("window_width", "512")
        store.set("window_height", 700)
        store.set("window_maximized", 1)

        raw = json.loads(settings_mod.path().read_text(encoding="utf-8"))
        self.assertEqual(raw["window_width"], 512)
        self.assertEqual(raw["window_height"], 700)
        self.assertIs(raw["window_maximized"], True)

        reloaded = settings_mod.Settings()
        self.assertEqual(reloaded.get("window_width"), 512)
        self.assertEqual(reloaded.get("window_height"), 700)
        self.assertTrue(reloaded.get("window_maximized"))

    def test_start_hidden_and_bad_values(self) -> None:
        store = settings_mod.Settings()
        store.set("start_hidden", "yes")
        self.assertTrue(store.get("start_hidden"))

        store.set("window_width", "wide")
        self.assertEqual(store.get("window_width"), 430)

    def test_hotkey_mapping_round_trip(self) -> None:
        store = settings_mod.Settings()
        self.assertEqual(store.get("fn_actions"), {"148": "toggle_window"})
        store.set("fn_actions", {148: "profile_next", 203: "keyboard_toggle"})
        self.assertEqual(
            settings_mod.Settings().get("fn_actions"),
            {"148": "profile_next", "203": "keyboard_toggle"},
        )
        store.set("fn_actions", "not a mapping")
        self.assertEqual(store.get("fn_actions"), {"148": "toggle_window"})

    def _write(self, data: dict) -> None:
        settings_mod.path().parent.mkdir(parents=True, exist_ok=True)
        settings_mod.path().write_text(json.dumps(data), encoding="utf-8")

    def test_v1_rog_mapping_is_migrated(self) -> None:
        self._write({"fn_actions": {"202": "toggle_window"}, "window_width": 500})
        store = settings_mod.Settings()
        self.assertEqual(store.get("fn_actions"), {"148": "toggle_window"})
        self.assertEqual(store.get("window_width"), 500)
        raw = json.loads(settings_mod.path().read_text(encoding="utf-8"))
        self.assertEqual(raw["settings_version"], settings_mod.SETTINGS_VERSION)

    def test_migration_moves_202_and_keeps_other_keys(self) -> None:
        self._write(
            {"fn_actions": {"202": "toggle_window", "248": "keyboard_toggle"}}
        )
        self.assertEqual(
            settings_mod.Settings().get("fn_actions"),
            {"148": "toggle_window", "248": "keyboard_toggle"},
        )

    def test_migration_keeps_custom_use_of_202(self) -> None:
        mappings = {"202": "profile_next", "248": "mic"}
        self._write({"fn_actions": mappings})
        store = settings_mod.Settings()
        self.assertEqual(store.get("fn_actions"), mappings)
        self.assertEqual(store.get("settings_version"), settings_mod.SETTINGS_VERSION)

    def test_migrated_settings_are_not_rewritten_again(self) -> None:
        self._write({"fn_actions": {"202": "toggle_window"}})
        settings_mod.Settings()
        settings_mod.path().write_text(
            json.dumps({"fn_actions": {"202": "toggle_window"}, "settings_version": 2}),
            encoding="utf-8",
        )
        self.assertEqual(
            settings_mod.Settings().get("fn_actions"), {"202": "toggle_window"}
        )

    def test_unknown_keys_are_not_persisted(self) -> None:
        store = settings_mod.Settings()
        store.set("window_width", 480)
        store.set("made_up", True)
        self.assertIsNone(store.get("made_up"))
        self.assertNotIn(
            "made_up",
            json.loads(settings_mod.path().read_text(encoding="utf-8")),
        )


if __name__ == "__main__":
    unittest.main()
