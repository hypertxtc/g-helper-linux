# SPDX-License-Identifier: GPL-3.0-or-later

"""A failing page refresh must not retry itself.

Settings used to call ``refresh()`` from its error handler, so an unavailable
asusd turned the refresh into a tight loop that flooded the journal (seen in a
real GUI smoke test: thousands of lines per second).  The retry-on-failure
behaviour is only safe for one-off actions, and even then the follow-up refresh
must use the non-retrying handler.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.ui import settings_window  # noqa: E402


def make_page():
    # Bypass Adw.Window.__init__: these tests only need the data plumbing.
    page = settings_window.SettingsWindow.__new__(settings_window.SettingsWindow)
    page._refresh_pending = False
    page.window = SimpleNamespace(run=mock.Mock(), show_error=mock.Mock())
    return page


class SettingsRefreshTests(unittest.TestCase):
    def test_refresh_uses_the_non_retrying_handler(self) -> None:
        page = make_page()
        page.refresh()
        error = page.window.run.call_args.kwargs["error"]
        self.assertIs(error.__func__, settings_window.SettingsWindow._load_failed)

    def test_failed_refresh_does_not_retry(self) -> None:
        page = make_page()
        page._refresh_pending = True
        page._load_failed(RuntimeError("asusd is down"))
        self.assertFalse(page._refresh_pending)
        page.window.show_error.assert_called_once_with(mock.ANY, quiet=True)
        page.window.run.assert_not_called()

    def test_failed_action_still_resyncs_once(self) -> None:
        page = make_page()
        page._failed(RuntimeError("write failed"))
        page.window.show_error.assert_called_once()
        page.window.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
