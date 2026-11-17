# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the cardwire launcher helpers (no cardwire needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import cardwire  # noqa: E402


class SpawnTests(unittest.TestCase):
    def tearDown(self) -> None:
        cardwire._spawned = []

    def test_builds_launch_argv(self) -> None:
        with mock.patch.object(cardwire, "available", return_value=True), mock.patch.object(
            cardwire.subprocess, "Popen"
        ) as popen:
            cardwire.spawn(["/usr/bin/steam", "-silent"])
        argv = popen.call_args.args[0]
        self.assertEqual(
            argv, ["cardwire", "launch", "--", "/usr/bin/steam", "-silent"]
        )
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(len(cardwire._spawned), 1)

    def test_gpu_option(self) -> None:
        with mock.patch.object(cardwire, "available", return_value=True), mock.patch.object(
            cardwire.subprocess, "Popen"
        ) as popen:
            cardwire.spawn(["/usr/bin/true"], gpu=1)
        self.assertEqual(
            popen.call_args.args[0],
            ["cardwire", "launch", "--gpu", "1", "--", "/usr/bin/true"],
        )

    def test_requires_cardwire(self) -> None:
        with mock.patch.object(cardwire, "available", return_value=False):
            with self.assertRaises(cardwire.CardwireError):
                cardwire.spawn(["/usr/bin/true"])

    def test_requires_a_command(self) -> None:
        with mock.patch.object(cardwire, "available", return_value=True):
            with self.assertRaises(cardwire.CardwireError):
                cardwire.spawn([])

    def test_oserror_becomes_cardwire_error(self) -> None:
        with mock.patch.object(cardwire, "available", return_value=True), mock.patch.object(
            cardwire.subprocess, "Popen", side_effect=OSError("boom")
        ):
            with self.assertRaises(cardwire.CardwireError):
                cardwire.spawn(["/usr/bin/true"])


class ReapTests(unittest.TestCase):
    class _Fake:
        def __init__(self, code: int | None) -> None:
            self.code = code

        def poll(self) -> int | None:
            return self.code

    def tearDown(self) -> None:
        cardwire._spawned = []

    def test_finished_launchers_are_dropped(self) -> None:
        running = self._Fake(None)
        finished = self._Fake(0)
        failed = self._Fake(1)
        cardwire._spawned = [running, finished, failed]
        cardwire.reap()
        self.assertEqual(cardwire._spawned, [running])


if __name__ == "__main__":
    unittest.main()
