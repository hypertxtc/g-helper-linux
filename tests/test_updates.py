# SPDX-License-Identifier: GPL-3.0-or-later

"""Headless unit tests for GitHub release update checking."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import updates  # noqa: E402


def _fake_response(payload: object) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class VersionComparisonTests(unittest.TestCase):
    def test_parse_version_handles_tags_suffixes_and_invalid(self) -> None:
        self.assertEqual(updates.parse_version("0.4.5"), (0, 4, 5))
        self.assertEqual(updates.parse_version("v0.4.5"), (0, 4, 5))
        self.assertEqual(updates.parse_version("V1.2.0-rc1"), (1, 2, 0))
        self.assertIsNone(updates.parse_version(""))
        self.assertIsNone(updates.parse_version("invalid"))

    def test_is_newer_comparisons(self) -> None:
        self.assertTrue(updates.is_newer("0.4.6", "0.4.5"))
        self.assertTrue(updates.is_newer("v0.10.0", "0.4.5"))
        self.assertTrue(updates.is_newer("0.5", "0.4.9"))
        self.assertFalse(updates.is_newer("0.4.5", "0.4.5"))
        self.assertFalse(updates.is_newer("v0.4.4", "0.4.5"))
        self.assertFalse(updates.is_newer("invalid", "0.4.5"))
        self.assertFalse(updates.is_newer("0.4.6", "invalid"))


class SummarizeNotesTests(unittest.TestCase):
    def test_strips_markdown_headings_and_respects_max_lines(self) -> None:
        body = (
            "## What's Changed\n\n"
            "* First item\n"
            "- Second item\n"
            "Third line\n"
            "Fourth line\n"
            "Fifth line\n"
        )
        self.assertEqual(
            updates.summarize_notes(body, max_lines=4),
            "What's Changed\nFirst item\nSecond item\nThird line",
        )


class CheckLatestTests(unittest.TestCase):
    def test_returns_available_when_newer_release_exists(self) -> None:
        payload = {
            "tag_name": "v0.4.6",
            "html_url": "https://github.com/hypertxtc/g-helper-linux/releases/tag/v0.4.6",
            "body": "### Highlights\n- In-app update check\n- Smooth upgrade\n",
            "assets": [
                {"name": "ghelper-linux_0.4.6-1_all.deb"},
                {"name": "ghelper-linux-0.4.6-1.fc45.noarch.rpm"},
            ],
        }
        with mock.patch.object(
            updates.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            info = updates.check_latest(current="0.4.5")
        self.assertTrue(info.available)
        self.assertEqual(info.current, "0.4.5")
        self.assertEqual(info.latest, "0.4.6")
        self.assertEqual(
            info.url, "https://github.com/hypertxtc/g-helper-linux/releases/tag/v0.4.6"
        )
        self.assertEqual(info.notes, "Highlights\nIn-app update check\nSmooth upgrade")
        self.assertEqual(
            info.assets,
            ("ghelper-linux_0.4.6-1_all.deb", "ghelper-linux-0.4.6-1.fc45.noarch.rpm"),
        )

    def test_returns_not_available_on_same_version(self) -> None:
        payload = {"tag_name": "v0.4.5"}
        with mock.patch.object(
            updates.urllib.request, "urlopen", return_value=_fake_response(payload)
        ):
            info = updates.check_latest(current="0.4.5")
        self.assertFalse(info.available)
        self.assertEqual(info.latest, "0.4.5")
        self.assertEqual(info.url, updates.RELEASE_PAGE)

    def test_network_error_raises_update_error(self) -> None:
        with mock.patch.object(
            updates.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            with self.assertRaises(updates.UpdateError):
                updates.check_latest(current="0.4.5")

    def test_missing_or_invalid_tag_raises_update_error(self) -> None:
        for payload in ({}, {"tag_name": "nightly"}):
            with self.subTest(payload=payload):
                with mock.patch.object(
                    updates.urllib.request,
                    "urlopen",
                    return_value=_fake_response(payload),
                ):
                    with self.assertRaises(updates.UpdateError):
                        updates.check_latest(current="0.4.5")


class StartupUpdateHookTests(unittest.TestCase):
    def _window(self, *, check_updates: bool = True, checked: bool = False):
        from ghelper.window import MainWindow

        win = MainWindow.__new__(MainWindow)
        win._update_checked = checked
        win.settings = mock.Mock()
        win.settings.get.side_effect = lambda key: check_updates if key == "check_updates" else None
        win.run = mock.Mock()
        return win

    def test_skips_when_disabled_in_settings(self) -> None:
        win = self._window(check_updates=False)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GHELPER_UI_TESTS", None)
            win.maybe_check_updates()
        win.run.assert_not_called()

    def test_skips_under_ui_tests_env(self) -> None:
        win = self._window(check_updates=True)
        with mock.patch.dict(os.environ, {"GHELPER_UI_TESTS": "1"}, clear=False):
            win.maybe_check_updates()
        win.run.assert_not_called()

    def test_runs_once_when_enabled(self) -> None:
        win = self._window(check_updates=True)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GHELPER_UI_TESTS", None)
            win.maybe_check_updates()
            win.maybe_check_updates()
        win.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
