# SPDX-License-Identifier: GPL-3.0-or-later

"""The shared D-Bus connection must carry a main loop.

dbus-python caches one connection per bus, and a connection created before a
main loop is set can never export objects.  The tray exports its
StatusNotifierItem on that connection, so a module that opens the session bus
during startup — the KWin helper — used to break the tray when it ran first.

Each case runs in a subprocess because the connection is process-wide: once
this test process has imported anything that sets the loop, the check would
pass for the wrong reason.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def default_loop_state(module: str) -> str:
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(SRC)!r})
        import {module}  # noqa: F401
        import dbus
        loop = dbus.get_default_main_loop()
        print("none" if loop is None else "set")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip().splitlines()[-1]


class MainLoopOrderTests(unittest.TestCase):
    def test_importing_the_app_sets_the_default_loop(self) -> None:
        self.assertEqual(default_loop_state("ghelper.app"), "set")

    def test_importing_the_kwin_helper_sets_the_default_loop(self) -> None:
        self.assertEqual(default_loop_state("ghelper.backend.kwin"), "set")

    def test_importing_the_tray_sets_the_default_loop(self) -> None:
        self.assertEqual(default_loop_state("ghelper.tray"), "set")

    def test_importing_display_sets_the_default_loop(self) -> None:
        self.assertEqual(default_loop_state("ghelper.backend.display"), "set")

    def test_importing_asusd_sets_the_default_loop(self) -> None:
        self.assertEqual(default_loop_state("ghelper.backend.asusd"), "set")

    def test_importing_ppd_sets_the_default_loop(self) -> None:
        self.assertEqual(default_loop_state("ghelper.backend.ppd"), "set")

    def test_importing_dbus_setup_initializes_glib_threads(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(SRC)!r})
            import ghelper.dbus_setup  # noqa: F401
            import dbus.mainloop.glib as glib_dbus
            print(glib_dbus._dbus_gthreads_initialized)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "True")


if __name__ == "__main__":
    unittest.main()
