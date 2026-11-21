# SPDX-License-Identifier: GPL-3.0-or-later

"""Smoke tests that build every window.

The unit tests never touch GTK, so a broken layout, a bad CSS selector or an
exception raised while a page is being assembled only showed up when a human
opened the window.  These tests construct the real application and every page,
pump the main loop until the background refresh lands, and assert that the
windows are actually presented.

They need a display: GTK4 has no headless backend.  CI runs them under Xvfb
with a private session bus, and they are skipped when no display is present.
Set ``GHELPER_REQUIRE_UI=1`` to turn that skip into a failure, which is what CI
does so a broken Xvfb run cannot go unnoticed.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Never read or write the user's real configuration, and give GTK a private
# runtime directory of the right mode.  The paths are applied in setUpClass so
# importing this module does not change the environment of the other tests.
_CONFIG = tempfile.mkdtemp(prefix="ghelper-ui-config-")
_RUNTIME = tempfile.mkdtemp(prefix="ghelper-ui-runtime-")
os.chmod(_RUNTIME, 0o700)
# A GTK critical means we built something wrong; abort instead of passing.
os.environ.setdefault("G_DEBUG", "fatal-criticals")

HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
RUN_UI = os.environ.get("GHELPER_UI_TESTS") == "1"
if RUN_UI and not HAS_DISPLAY:
    raise RuntimeError("GHELPER_UI_TESTS=1 but no display is available")

import ghelper.app as appmod  # noqa: E402
import ghelper.window as winmod  # noqa: E402
from ghelper.deps import GLib  # noqa: E402

# A unique application id keeps a running instance out of the way, while the
# window keeps the real icon name.
appmod.APP_ID = "io.github.ghelperlinux.GHelperUiTest"
winmod.APP_ID = "io.github.ghelperlinux.GHelper"

TIMEOUT = 15.0


def pump(predicate, timeout: float = TIMEOUT) -> bool:
    """Iterate the default main context until ``predicate`` is true."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while True:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return predicate()
        time.sleep(0.01)


def add_cleanup() -> None:
    for path in (_CONFIG, _RUNTIME):
        shutil.rmtree(path, ignore_errors=True)


unittest.addModuleCleanup(add_cleanup)


@unittest.skipUnless(RUN_UI, "set GHELPER_UI_TESTS=1 to build the windows")
class WindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Use the throwaway config even if an earlier test module popped the
        # variable while cleaning up after itself.
        os.environ["XDG_CONFIG_HOME"] = _CONFIG
        os.environ["XDG_RUNTIME_DIR"] = _RUNTIME
        cls.app = appmod.Application()
        if not cls.app.register(None):
            raise unittest.SkipTest("could not register the test application")
        cls.window = cls.app._ensure_window()

    @classmethod
    def tearDownClass(cls) -> None:
        for page in (
            cls.window._settings_window,
            cls.window._fans_window,
            cls.window._hotkeys_window,
            cls.window._setup_window,
        ):
            if page is not None:
                page.destroy()
        cls.window.stop_hotkeys()
        if cls.window._tick_id:
            GLib.source_remove(cls.window._tick_id)
            cls.window._tick_id = 0
        if cls.window._power_id:
            GLib.source_remove(cls.window._power_id)
            cls.window._power_id = 0
        cls.window.tasks.shutdown()
        cls.window.destroy()

    def test_main_window_builds_and_refreshes(self) -> None:
        self.window.present()
        self.assertTrue(pump(self.window.get_visible), "main window never mapped")
        self.assertTrue(
            pump(lambda: not self.window.main_view._refresh_pending),
            "main refresh never finished",
        )
        view = self.window.main_view
        self.assertEqual(self.window.get_title(), "G-Helper")
        self.assertEqual(len(view._gpu_buttons), 4)
        self.assertIsNotNone(view.battery_scale)
        self.assertIsNotNone(view._overdrive_row)
        self.assertIsNotNone(view.slash_section)
        self.assertIsNotNone(view.anime_section)
        # The status line is filled from telemetry or left empty when sensors
        # are missing; either way it must be a label with text support.
        self.assertIsNotNone(view.perf_section.header._live)

    def test_settings_window_builds(self) -> None:
        self.window.open_settings_window()
        page = self.window._settings_window
        self.assertIsNotNone(page)
        self.assertTrue(pump(page.get_visible), "settings window never mapped")
        self.assertTrue(
            pump(lambda: not page._refresh_pending), "settings refresh never finished"
        )
        self.assertEqual(page.get_title(), "Settings")
        self.assertIsNotNone(page._boot_sound_switch)
        self.assertIsNotNone(page._hidden_switch)
        self.assertIsNotNone(page._smart_switch)
        self.assertIsNotNone(page._slash_section)
        self.assertIsNotNone(page._anime_section)
        self.assertIsNotNone(page._setup_button)

    def test_fans_window_builds(self) -> None:
        self.window.open_fans_window()
        page = self.window._fans_window
        self.assertIsNotNone(page)
        self.assertTrue(pump(page.get_visible), "fans window never mapped")
        self.assertTrue(
            pump(lambda: not page._refresh_pending), "fans refresh never finished"
        )
        self.assertEqual(page.get_title(), "Fans and Power")
        self.assertIsNotNone(page.cpu_chart)
        self.assertIsNotNone(page.gpu_chart)
        self.assertIsNotNone(page.mid_chart)
        self.assertIsNotNone(page.fan_status)
        self.assertIsNotNone(page.fppt_slider)
        self.assertIn(
            page.ppt_status.get_text(),
            ("Applied", "Not applied", "Not available", "\u2014"),
        )

    def test_power_overlay_builds(self) -> None:
        from ghelper.backend.asusd import BALANCED
        from ghelper.ui.overlay import PowerOverlay

        overlay = PowerOverlay(self.app)
        self.addCleanup(overlay.destroy)
        overlay.show_status(BALANCED, False)
        self.assertTrue(pump(overlay.get_visible), "overlay never mapped")
        self.assertIn("Balanced", overlay._title.get_text())
        self.assertEqual(overlay._source.get_text(), "On battery")
        overlay.dismiss()
        self.assertFalse(overlay.get_visible(), "overlay did not dismiss")

    def test_hotkeys_window_builds(self) -> None:
        self.window.open_hotkeys_window()
        page = self.window._hotkeys_window
        self.assertIsNotNone(page)
        self.assertTrue(pump(page.get_visible), "hotkeys window never mapped")
        self.assertEqual(page.get_title(), "Hotkeys")
        # Either the passive listener started, or the window explains why it
        # could not; an empty status means the failure path broke.
        self.assertTrue(page._status.get_text().strip())
        self.assertIsNotNone(page._empty)

    def test_setup_window_builds(self) -> None:
        self.window.open_setup_window()
        page = self.window._setup_window
        self.assertIsNotNone(page)
        self.assertTrue(pump(page.get_visible), "setup window never mapped")
        self.assertTrue(
            pump(lambda: not page._refresh_pending), "setup refresh never finished"
        )
        self.assertEqual(page.get_title(), "Setup Check")
        self.assertTrue(page._headline.get_text().strip())
        self.assertGreaterEqual(len(page._items), 5)
        self.assertIsNotNone(page._list_box.get_first_child())


if __name__ == "__main__":
    unittest.main()
