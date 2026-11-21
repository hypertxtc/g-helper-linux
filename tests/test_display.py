# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop gating, parsers and apply builders for the display backends."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import display  # noqa: E402

KSCREEN = "/usr/bin/kscreen-doctor"
HYPRCTL = "/usr/bin/hyprctl"
WLR_RANDR = "/usr/bin/wlr-randr"


def env(**values: str) -> dict[str, str]:
    """A clean environment with only the desktop variables that were given."""
    return dict(values)


def fake_which(available_bins: set[str]):
    paths = {
        "kscreen-doctor": KSCREEN,
        "hyprctl": HYPRCTL,
        "wlr-randr": WLR_RANDR,
    }
    return lambda name: paths.get(name) if name in available_bins else None


class DesktopTests(unittest.TestCase):
    def test_kde_is_detected_from_the_current_desktop(self) -> None:
        with mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="KDE"), clear=True):
            self.assertTrue(display.is_kde())
            self.assertFalse(display.is_gnome())

    def test_desktop_lists_are_split_and_case_folded(self) -> None:
        with mock.patch.dict(
            os.environ, env(XDG_CURRENT_DESKTOP="ubuntu:KDE;X-Custom"), clear=True
        ):
            self.assertTrue(display.is_kde())

    def test_session_desktop_is_a_fallback(self) -> None:
        with mock.patch.dict(os.environ, env(XDG_SESSION_DESKTOP="KDE"), clear=True):
            self.assertTrue(display.is_kde())

    def test_gnome_detection(self) -> None:
        for name in ("GNOME", "ubuntu:GNOME", "GNOME-Classic:GNOME"):
            with self.subTest(desktop=name):
                with mock.patch.dict(
                    os.environ, env(XDG_CURRENT_DESKTOP=name), clear=True
                ):
                    self.assertTrue(display.is_gnome())
                    self.assertFalse(display.is_kde())

    def test_hyprland_and_wlroots_detection(self) -> None:
        with mock.patch.dict(
            os.environ, env(XDG_CURRENT_DESKTOP="Hyprland"), clear=True
        ):
            self.assertTrue(display.is_hyprland())
        with mock.patch.dict(
            os.environ, env(HYPRLAND_INSTANCE_SIGNATURE="abc123"), clear=True
        ):
            self.assertTrue(display.is_hyprland())
        for name in ("sway", "wayfire", "river", "labwc", "niri"):
            with self.subTest(desktop=name):
                with mock.patch.dict(
                    os.environ, env(XDG_CURRENT_DESKTOP=name), clear=True
                ):
                    self.assertTrue(display.is_wlroots())

    def test_other_desktops_are_not_kde(self) -> None:
        for name in ("GNOME", "ubuntu:GNOME", "sway", "Hyprland", "XFCE"):
            with self.subTest(desktop=name):
                with mock.patch.dict(
                    os.environ, env(XDG_CURRENT_DESKTOP=name), clear=True
                ):
                    self.assertFalse(display.is_kde())


class AvailabilityTests(unittest.TestCase):
    def test_available_on_kde_with_the_binary(self) -> None:
        with (
            mock.patch.object(
                display.shutil, "which", side_effect=fake_which({"kscreen-doctor"})
            ),
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="KDE"), clear=True),
        ):
            self.assertEqual(display.backend_name(), "kscreen")
            self.assertTrue(display.available())

    def test_missing_binary_disables_kde(self) -> None:
        with (
            mock.patch.object(display.shutil, "which", return_value=None),
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="KDE"), clear=True),
        ):
            self.assertIsNone(display.backend_name())
            self.assertFalse(display.available())
            self.assertIn("kscreen-doctor", display.unavailable_reason())

    def test_gnome_uses_mutter_and_never_spawns_kscreen(self) -> None:
        with (
            mock.patch.object(
                display.shutil, "which", side_effect=fake_which({"kscreen-doctor"})
            ),
            mock.patch.object(display, "_mutter_available", return_value=True),
            mock.patch.dict(
                os.environ,
                env(XDG_CURRENT_DESKTOP="GNOME", XDG_SESSION_DESKTOP="gnome"),
                clear=True,
            ),
        ):
            self.assertEqual(display.backend_name(), "mutter")
            self.assertTrue(display.available())

        with (
            mock.patch.object(
                display.shutil, "which", side_effect=fake_which({"kscreen-doctor"})
            ),
            mock.patch.object(display, "_mutter_available", return_value=False),
            mock.patch.object(display.subprocess, "run") as run,
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="GNOME"), clear=True),
        ):
            self.assertFalse(display.available())
            self.assertIn("Mutter", display.unavailable_reason())
            self.assertEqual(display.outputs(), [])
            with self.assertRaises(display.DisplayError):
                display.set_refresh_rate("eDP-1", 2560, 1600, 60.0)
        run.assert_not_called()

    def test_hyprland_prefers_hyprctl_and_falls_back_to_wlr_randr(self) -> None:
        with (
            mock.patch.object(
                display.shutil,
                "which",
                side_effect=fake_which({"hyprctl", "wlr-randr"}),
            ),
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="Hyprland"), clear=True),
        ):
            self.assertEqual(display.backend_name(), "hyprland")

        with (
            mock.patch.object(
                display.shutil, "which", side_effect=fake_which({"wlr-randr"})
            ),
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="Hyprland"), clear=True),
        ):
            self.assertEqual(display.backend_name(), "wlr-randr")

        with (
            mock.patch.object(display.shutil, "which", return_value=None),
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="Hyprland"), clear=True),
        ):
            self.assertFalse(display.available())
            self.assertIn("hyprctl", display.unavailable_reason())

    def test_wlroots_requires_wlr_randr_and_ignores_kscreen(self) -> None:
        with (
            mock.patch.object(
                display.shutil,
                "which",
                side_effect=fake_which({"kscreen-doctor", "wlr-randr"}),
            ),
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="sway"), clear=True),
        ):
            self.assertEqual(display.backend_name(), "wlr-randr")

        with (
            mock.patch.object(
                display.shutil, "which", side_effect=fake_which({"kscreen-doctor"})
            ),
            mock.patch.object(display.subprocess, "run") as run,
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="sway"), clear=True),
        ):
            self.assertFalse(display.available())
            self.assertIn("wlr-randr", display.unavailable_reason())
            self.assertEqual(display.outputs(), [])
        run.assert_not_called()

    def test_unknown_and_unsupported_desktops(self) -> None:
        with (
            mock.patch.object(
                display.shutil, "which", side_effect=fake_which({"kscreen-doctor"})
            ),
            mock.patch.object(display, "_mutter_available", return_value=False),
            mock.patch.dict(os.environ, env(), clear=True),
        ):
            self.assertEqual(display.backend_name(), "kscreen")

        with (
            mock.patch.object(
                display.shutil, "which", side_effect=fake_which({"wlr-randr"})
            ),
            mock.patch.object(display, "_mutter_available", return_value=False),
            mock.patch.dict(os.environ, env(), clear=True),
        ):
            self.assertEqual(display.backend_name(), "wlr-randr")

        with (
            mock.patch.object(display.shutil, "which", return_value=None),
            mock.patch.object(display, "_mutter_available", return_value=True),
            mock.patch.dict(os.environ, env(), clear=True),
        ):
            self.assertEqual(display.backend_name(), "mutter")

        with (
            mock.patch.object(
                display.shutil,
                "which",
                side_effect=fake_which({"kscreen-doctor", "wlr-randr"}),
            ),
            mock.patch.object(display, "_mutter_available", return_value=True),
            mock.patch.dict(os.environ, env(XDG_CURRENT_DESKTOP="XFCE"), clear=True),
        ):
            self.assertIsNone(display.backend_name())
            self.assertFalse(display.available())


class MutterTests(unittest.TestCase):
    SAMPLE_STATE = (
        7,
        [
            (
                ("eDP-1", "AUO", "0x1234", "0001"),
                [
                    (
                        "2560x1600@120.000",
                        2560,
                        1600,
                        120.0,
                        2.0,
                        [1.0, 2.0],
                        {"is-current": True, "is-preferred": True},
                    ),
                    ("2560x1600@60.000", 2560, 1600, 60.0, 2.0, [1.0, 2.0], {}),
                ],
                {"is-builtin": True},
            ),
            (
                ("DP-1", "DEL", "U2723QE", "999"),
                [
                    ("3840x2160@60.000", 3840, 2160, 60.0, 2.0, [1.0, 2.0], {"is-current": True}),
                ],
                {"is-builtin": False},
            ),
        ],
        [
            (0, 0, 2.0, 0, True, [("eDP-1", "AUO", "0x1234", "0001")], {}),
            (1280, 0, 1.5, 1, False, [("DP-1", "DEL", "U2723QE", "999")], {}),
        ],
        {"layout-mode": 1},
    )

    def test_parse_mutter_state(self) -> None:
        outputs = display.parse_mutter_state(self.SAMPLE_STATE)
        self.assertEqual(len(outputs), 2)
        panel = outputs[0]
        self.assertEqual(panel["name"], "eDP-1")
        self.assertTrue(panel["enabled"])
        self.assertTrue(panel["panel"])
        self.assertEqual(panel["scale"], 2.0)
        self.assertEqual(panel["geometry"], (2560, 1600))
        self.assertEqual(display.refresh_rates(panel), [120.0, 60.0])
        self.assertFalse(outputs[1]["panel"])
        self.assertEqual(outputs[1]["scale"], 1.5)

    def test_build_mutter_apply_args_preserves_other_monitors(self) -> None:
        serial, method, logical, props = display.build_mutter_apply_args(
            self.SAMPLE_STATE, "eDP-1", 2560, 1600, 60.0
        )
        self.assertEqual(serial, 7)
        self.assertEqual(method, display.MUTTER_METHOD_TEMPORARY)
        self.assertEqual(props, {})
        self.assertEqual(
            logical,
            [
                (0, 0, 2.0, 0, True, [("eDP-1", "2560x1600@60.000", {})]),
                (1280, 0, 1.5, 1, False, [("DP-1", "3840x2160@60.000", {})]),
            ],
        )

    def test_build_mutter_apply_args_layout_mode_capability(self) -> None:
        serial, monitors, logical_monitors, _props = self.SAMPLE_STATE
        unsupported = (
            serial,
            monitors,
            logical_monitors,
            {"layout-mode": 1, "supports-changing-layout-mode": False},
        )
        _s, _m, _l, props = display.build_mutter_apply_args(
            unsupported, "eDP-1", 2560, 1600, 60.0
        )
        self.assertEqual(props, {})

        supported = (
            serial,
            monitors,
            logical_monitors,
            {"layout-mode": 2, "supports-changing-layout-mode": True},
        )
        _s, _m, _l, props = display.build_mutter_apply_args(
            supported, "eDP-1", 2560, 1600, 60.0
        )
        self.assertEqual(props, {"layout-mode": 2})

    def test_build_mutter_apply_args_errors(self) -> None:
        with self.assertRaises(display.DisplayError):
            display.build_mutter_apply_args(self.SAMPLE_STATE, "HDMI-1", 1920, 1080, 60.0)
        with self.assertRaises(display.DisplayError):
            display.build_mutter_apply_args(self.SAMPLE_STATE, "eDP-1", 2560, 1600, 144.0)

        inactive = (
            1,
            [self.SAMPLE_STATE[1][0]],
            [],
            {},
        )
        with self.assertRaises(display.DisplayError):
            display.build_mutter_apply_args(inactive, "eDP-1", 2560, 1600, 60.0)

    def test_mutter_set_refresh_rate_calls_apply(self) -> None:
        proxy = mock.Mock()
        proxy.GetCurrentState.return_value = self.SAMPLE_STATE
        with mock.patch.object(display, "_mutter_proxy", return_value=proxy):
            display._mutter_set_refresh_rate("eDP-1", 2560, 1600, 60.0)
        proxy.ApplyMonitorsConfig.assert_called_once()
        args = proxy.ApplyMonitorsConfig.call_args.args
        self.assertEqual(int(args[0]), 7)
        self.assertEqual(int(args[1]), display.MUTTER_METHOD_TEMPORARY)
        self.assertEqual(dict(args[3]), {})


class HyprlandTests(unittest.TestCase):
    SAMPLE = json.dumps(
        [
            {
                "id": 0,
                "name": "eDP-1",
                "width": 2560,
                "height": 1600,
                "refreshRate": 120.0,
                "x": 0,
                "y": 0,
                "scale": 1.5,
                "transform": 2,
                "disabled": False,
                "availableModes": [
                    "2560x1600@120.00Hz",
                    "2560x1600@60.00Hz",
                    "1920x1080@60.00Hz",
                ],
            }
        ]
    )

    def test_parse_hyprland_outputs(self) -> None:
        outputs = display.parse_hyprland_outputs(self.SAMPLE)
        self.assertEqual(len(outputs), 1)
        panel = outputs[0]
        self.assertEqual(panel["name"], "eDP-1")
        self.assertTrue(panel["enabled"])
        self.assertTrue(panel["panel"])
        self.assertEqual(panel["scale"], 1.5)
        self.assertEqual(panel["geometry"], (2560, 1600))
        self.assertEqual(display.refresh_rates(panel), [120.0, 60.0])

    def test_hyprland_set_refresh_rate_preserves_transform(self) -> None:
        with mock.patch.object(display, "_run_tool", side_effect=[self.SAMPLE, ""]) as run:
            display._hyprland_set_refresh_rate("eDP-1", 2560, 1600, 60.0)
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["hyprctl", "keyword", "monitor", "eDP-1,2560x1600@60.00,0x0,1.5,transform,2"],
        )


class WlrRandrTests(unittest.TestCase):
    def test_parse_wlr_list_and_dict_shapes(self) -> None:
        list_json = json.dumps(
            [
                {
                    "name": "eDP-1",
                    "enabled": True,
                    "scale": 1.25,
                    "current_mode": {"width": 2560, "height": 1600, "refresh": 120000000},
                    "modes": [
                        {"width": 2560, "height": 1600, "refresh": 120000000, "preferred": True},
                        {"width": 2560, "height": 1600, "refresh": 60000, "preferred": False},
                    ],
                }
            ]
        )
        outputs = display.parse_wlr_outputs(list_json)
        self.assertEqual(len(outputs), 1)
        panel = outputs[0]
        self.assertTrue(panel["panel"])
        self.assertEqual(panel["geometry"], (2560, 1600))
        self.assertEqual(display.current_mode(panel)["rate"], 120.0)
        self.assertEqual(display.refresh_rates(panel), [120.0, 60.0])

        dict_json = json.dumps(
            {
                "eDP-2": {
                    "enabled": True,
                    "scale": 1.0,
                    "modes": [
                        {"width": 1920, "height": 1080, "refresh": 144.0, "current": True},
                        {"width": 1920, "height": 1080, "refresh": 60.0, "current": False},
                    ],
                }
            }
        )
        outputs2 = display.parse_wlr_outputs(dict_json)
        self.assertEqual(outputs2[0]["name"], "eDP-2")
        self.assertEqual(display.refresh_rates(outputs2[0]), [144.0, 60.0])

    def test_parse_wlr_text_fallback(self) -> None:
        sample = (
            'eDP-1 "BOE 0x095F (eDP-1)"\n'
            "  Enabled: yes\n"
            "  Modes:\n"
            "    2560x1600 px, 120.000000 Hz (preferred, current)\n"
            "    2560x1600 px, 60.000000 Hz\n"
            "  Scale: 1.500000\n"
        )
        outputs = display.parse_wlr_text(sample)
        self.assertEqual(len(outputs), 1)
        panel = outputs[0]
        self.assertEqual(panel["name"], "eDP-1")
        self.assertTrue(panel["enabled"])
        self.assertTrue(panel["panel"])
        self.assertEqual(panel["scale"], 1.5)
        self.assertEqual(display.refresh_rates(panel), [120.0, 60.0])


if __name__ == "__main__":
    unittest.main()
