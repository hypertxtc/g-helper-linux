# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop gating for the refresh-rate backend.

``kscreen-doctor`` only works on KDE, so its presence is not enough: on a
GNOME or wlroots session the row must stay read-only instead of failing with a
KWin error.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import display  # noqa: E402

BINARY = "/usr/bin/kscreen-doctor"


def env(**values: str) -> dict[str, str]:
    """A clean environment with only the desktop variables that were given."""
    return dict(values)


class DesktopTests(unittest.TestCase):
    def test_kde_is_detected_from_the_current_desktop(self) -> None:
        with mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="KDE"), clear=True):
            self.assertTrue(display.is_kde())

    def test_desktop_lists_are_split_and_case_folded(self) -> None:
        with mock.patch.dict(
            os.environ, env(XDG_CURRENT_DESKTOP="ubuntu:KDE;X-Custom"), clear=True
        ):
            self.assertTrue(display.is_kde())

    def test_session_desktop_is_a_fallback(self) -> None:
        with mock.patch.dict(os.environ, env(XDG_SESSION_DESKTOP="KDE"), clear=True):
            self.assertTrue(display.is_kde())

    def test_other_desktops_are_not_kde(self) -> None:
        for name in ("GNOME", "ubuntu:GNOME", "sway", "Hyprland", "XFCE"):
            with self.subTest(desktop=name):
                with mock.patch.dict(
                    os.environ, env(XDG_CURRENT_DESKTOP=name), clear=True
                ):
                    self.assertFalse(display.is_kde())


class AvailabilityTests(unittest.TestCase):
    def test_available_on_kde_with_the_binary(self) -> None:
        with mock.patch.object(display.shutil, "which", return_value=BINARY):
            with mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="KDE"), clear=True):
                self.assertTrue(display.available())

    def test_missing_binary_disables_the_backend(self) -> None:
        with mock.patch.object(display.shutil, "which", return_value=None):
            with mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="KDE"), clear=True):
                self.assertFalse(display.available())
                self.assertIn("kscreen-doctor", display.unavailable_reason())

    def test_gnome_disables_the_backend_even_with_the_binary(self) -> None:
        with mock.patch.object(display.shutil, "which", return_value=BINARY):
            with mock.patch.dict(
                os.environ, env(XDG_CURRENT_DESKTOP="GNOME", XDG_SESSION_DESKTOP="gnome"),
                clear=True,
            ):
                self.assertFalse(display.available())
                self.assertEqual(
                    display.unavailable_reason(),
                    "Refresh-rate switching needs KDE Plasma",
                )

    def test_wlroots_disables_the_backend(self) -> None:
        with mock.patch.object(display.shutil, "which", return_value=BINARY):
            with mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="sway"), clear=True):
                self.assertFalse(display.available())

    def test_unknown_desktop_still_tries(self) -> None:
        # Started from a TTY or a unit without the session variables: the
        # binary is the only evidence available, so do not refuse outright.
        with mock.patch.object(display.shutil, "which", return_value=BINARY):
            with mock.patch.dict(os.environ, env(), clear=True):
                self.assertTrue(display.available())

    def test_outputs_do_not_spawn_kscreen_doctor_when_disabled(self) -> None:
        with mock.patch.object(display.shutil, "which", return_value=BINARY):
            with mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="GNOME"), clear=True):
                with mock.patch.object(display.subprocess, "run") as run:
                    self.assertEqual(display.outputs(), [])
                    self.assertIsNone(display.primary_panel())
        run.assert_not_called()

    def test_set_refresh_rate_refuses_before_spawning(self) -> None:
        with mock.patch.object(display.shutil, "which", return_value=BINARY):
            with mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="GNOME"), clear=True):
                with mock.patch.object(display.subprocess, "run") as run:
                    with self.assertRaises(display.DisplayError):
                        display.set_refresh_rate("eDP-1", 2560, 1600, 60.0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
