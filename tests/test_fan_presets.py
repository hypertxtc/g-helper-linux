# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for fan-curve presets, validation and JSON import / export."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import fan_presets  # noqa: E402

# Validation failures are expected here; keep the test output readable.
fan_presets.log.setLevel(logging.CRITICAL)


def curve(temps: list[int], pwm: list[int], enabled: bool = True) -> dict:
    return {"temp": temps, "pwm": pwm, "enabled": enabled}


def sample() -> dict:
    return {
        "CPU": curve([35, 45, 55, 65, 72, 78, 85, 95], [20, 45, 70, 95, 120, 150, 185, 220]),
        "GPU": curve([35, 45, 55, 65, 72, 78, 85, 95], [30, 55, 80, 105, 130, 160, 195, 240]),
    }


class NormalizeTests(unittest.TestCase):
    def test_accepts_both_fans(self) -> None:
        cleaned = fan_presets.normalize(sample())
        self.assertEqual(sorted(cleaned), ["CPU", "GPU"])
        self.assertEqual(cleaned["CPU"]["temp"][0], 35)
        self.assertTrue(cleaned["CPU"]["enabled"])

    def test_coerces_numeric_strings(self) -> None:
        cleaned = fan_presets.normalize(
            {"CPU": {"temp": [str(v) for v in range(8)], "pwm": [0] * 8}}
        )
        self.assertEqual(cleaned["CPU"]["temp"], list(range(8)))

    def test_rejects_wrong_point_count(self) -> None:
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.normalize({"CPU": curve([10, 20], [30, 40])})

    def test_rejects_unordered_temperatures(self) -> None:
        bad = {**sample()}
        bad["CPU"] = curve([35, 45, 40, 65, 72, 78, 85, 95], [20] * 8)
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.normalize(bad)

    def test_rejects_out_of_range_values(self) -> None:
        hot = {**sample()}
        hot["GPU"] = curve([35, 45, 55, 65, 72, 78, 85, 140], [30] * 8)
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.normalize(hot)
        loud = {**sample()}
        loud["GPU"] = curve([35, 45, 55, 65, 72, 78, 85, 95], [0, 0, 0, 0, 0, 0, 0, 300])
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.normalize(loud)

    def test_rejects_unknown_fan_and_empty(self) -> None:
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.normalize({**sample(), "MID": sample()["CPU"]})
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.normalize({})
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.normalize("nope")


class NameTests(unittest.TestCase):
    def test_collapses_whitespace(self) -> None:
        self.assertEqual(fan_presets.clean_name("  Quiet   night \n"), "Quiet night")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.clean_name("   ")

    def test_long_names_are_truncated(self) -> None:
        name = fan_presets.clean_name("x" * 200)
        self.assertEqual(len(name), fan_presets.NAME_LIMIT)

    def test_unique_name_adds_suffix(self) -> None:
        self.assertEqual(fan_presets.unique_name("Turbo", []), "Turbo")
        self.assertEqual(fan_presets.unique_name("Turbo", ["Turbo"]), "Turbo (2)")
        self.assertEqual(
            fan_presets.unique_name("Turbo", ["Turbo", "Turbo (2)"]), "Turbo (3)"
        )


class LibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(os.environ.pop, "XDG_CONFIG_HOME", None)
        os.environ["XDG_CONFIG_HOME"] = self._dir.name

    def test_round_trip(self) -> None:
        name, entry = fan_presets.add("Night", sample())
        self.assertEqual(name, "Night")
        self.assertTrue(entry["created"])

        library = fan_presets.load()
        self.assertEqual(list(library), ["Night"])
        self.assertEqual(library["Night"]["curves"]["GPU"]["pwm"][-1], 240)
        self.assertTrue(fan_presets.path().exists())
        self.assertEqual(fan_presets.path().name, "fan-curves.json")

    def test_add_overwrites_and_remove(self) -> None:
        fan_presets.add("Night", sample())
        fan_presets.add("Night", {"CPU": sample()["CPU"]})
        self.assertEqual(list(fan_presets.load()["Night"]["curves"]), ["CPU"])

        self.assertTrue(fan_presets.remove("Night"))
        self.assertFalse(fan_presets.remove("Night"))
        self.assertEqual(fan_presets.load(), {})

    def test_corrupt_library_is_ignored(self) -> None:
        fan_presets.path().parent.mkdir(parents=True, exist_ok=True)
        fan_presets.path().write_text("{not json", encoding="utf-8")
        self.assertEqual(fan_presets.load(), {})

        fan_presets.path().write_text('{"presets": {"ok": {"curves": {}}}}', encoding="utf-8")
        self.assertEqual(fan_presets.load(), {})

    def test_invalid_presets_are_skipped(self) -> None:
        fan_presets.path().parent.mkdir(parents=True, exist_ok=True)
        document = {
            "version": 1,
            "presets": {
                "good": {"created": "2026-01-01T00:00:00", "curves": sample()},
                "bad": {"curves": {"CPU": curve([50, 40], [1, 2])}},
            },
        }
        fan_presets.path().write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(list(fan_presets.load()), ["good"])


class FileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.addCleanup(os.environ.pop, "XDG_CONFIG_HOME", None)
        os.environ["XDG_CONFIG_HOME"] = self._dir.name
        self.target = Path(self._dir.name) / "out" / "curve.json"
        self.target.parent.mkdir(parents=True, exist_ok=True)

    def test_export_shape(self) -> None:
        name = fan_presets.export_file(self.target, "  Turbo  ", sample())
        self.assertEqual(name, "Turbo")
        raw = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)
        self.assertIn("Turbo", raw["presets"])
        self.assertEqual(raw["presets"]["Turbo"]["curves"]["CPU"]["pwm"][0], 20)

    def test_import_round_trip(self) -> None:
        fan_presets.export_file(self.target, "Turbo", sample())
        added = fan_presets.import_file(self.target)
        self.assertEqual(added, ["Turbo"])
        self.assertEqual(fan_presets.load()["Turbo"]["curves"], fan_presets.normalize(sample()))

    def test_import_renames_duplicates(self) -> None:
        fan_presets.add("Turbo", sample())
        fan_presets.export_file(self.target, "Turbo", sample())
        added = fan_presets.import_file(self.target)
        self.assertEqual(added, ["Turbo (2)"])
        self.assertEqual(sorted(fan_presets.load()), ["Turbo", "Turbo (2)"])

    def test_import_single_preset_without_presets_key(self) -> None:
        document = {"name": "Laptop stand", "curves": sample()}
        self.target.write_text(json.dumps(document), encoding="utf-8")
        self.assertEqual(fan_presets.import_file(self.target), ["Laptop stand"])

    def test_import_rejects_junk(self) -> None:
        self.target.write_text("[]", encoding="utf-8")
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.import_file(self.target)

        self.target.write_text('{"presets": {"bad": {"curves": {}}}}', encoding="utf-8")
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.import_file(self.target)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(fan_presets.CurveError):
            fan_presets.import_file(self.target)


class SummaryTests(unittest.TestCase):
    def test_summary(self) -> None:
        self.assertIn("CPU 35\u201395", fan_presets.summary(sample()))
        self.assertEqual(fan_presets.summary({}), "no curves")


if __name__ == "__main__":
    unittest.main()
