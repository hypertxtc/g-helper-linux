# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for FansWindow, FanChart telemetry, MID fan, fPPT, and VerticalSlider."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import asusd as asusd_mod  # noqa: E402
from ghelper.backend import system  # noqa: E402
from ghelper.ui import chart as chart_mod  # noqa: E402
from ghelper.ui import fans_window as fw_mod  # noqa: E402
from ghelper.ui import widgets as widgets_mod  # noqa: E402


class FakeChart:
    def __init__(self, pwm=None, temp=None, *, visible: bool = True) -> None:
        self._pwm = list(pwm or [20, 40, 60, 80, 100, 130, 160, 200])
        self._temp = list(temp or [40, 50, 60, 65, 70, 75, 80, 90])
        self._visible = visible
        self._sensitive = True
        self.size_requests: list[tuple[int, int]] = []
        self.live_calls: list[tuple] = []

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)

    def get_visible(self) -> bool:
        return self._visible

    def set_sensitive(self, sensitive: bool) -> None:
        self._sensitive = bool(sensitive)

    def get_sensitive(self) -> bool:
        return self._sensitive

    def set_size_request(self, width: int, height: int) -> None:
        self.size_requests.append((width, height))

    def set_points(self, pwm: list[int], temp: list[int]) -> None:
        self._pwm = list(pwm)
        self._temp = list(temp)

    def points(self) -> tuple[list[int], list[int]]:
        return list(self._pwm), list(self._temp)

    def set_live(self, temp, rpm=None, pwm=None) -> None:
        self.live_calls.append((temp, rpm, pwm))


class FakeSlider:
    def __init__(self, value: float = 50.0) -> None:
        self._lower = 0.0
        self._upper = 100.0
        self._value = value
        self._sensitive = True
        self._visible = True

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)

    def get_visible(self) -> bool:
        return self._visible

    def set_sensitive(self, sensitive: bool) -> None:
        self._sensitive = bool(sensitive)

    def get_sensitive(self) -> bool:
        return self._sensitive

    def set_range(self, lower: float, upper: float) -> None:
        self._lower = lower
        self._upper = upper
        self._value = max(lower, min(upper, self._value))

    def set_value(self, value: float) -> None:
        self._value = max(self._lower, min(self._upper, value))


class FakeScale:
    def __init__(self, lower: float, upper: float, value: float, on_changed) -> None:
        self.lower = lower
        self.upper = upper
        self.value = value
        self.on_changed = on_changed

    def get_adjustment(self):
        return SimpleNamespace(
            get_lower=lambda: self.lower,
            get_upper=lambda: self.upper,
        )

    def get_value(self) -> float:
        return self.value

    def set_value(self, value: float) -> None:
        clamped = max(self.lower, min(self.upper, value))
        if clamped != self.value:
            self.value = clamped
            self.on_changed(self)

    def set_range(self, lower: float, upper: float) -> None:
        self.lower = lower
        self.upper = upper
        clamped = max(lower, min(upper, self.value))
        if clamped != self.value:
            self.value = clamped
            self.on_changed(self)


def make_label() -> SimpleNamespace:
    label = SimpleNamespace(text="", classes=set())
    label.set_text = lambda text: setattr(label, "text", text)
    label.add_css_class = lambda name: label.classes.add(name)
    label.remove_css_class = lambda name: label.classes.discard(name)
    return label


def run_sync_window_tasks(win) -> None:
    def submit(work, *, done=None, error=None):
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            if error is not None:
                error(exc)
        else:
            if done is not None:
                done(result)

    win.window.run = mock.Mock(side_effect=submit)


def make_fans_window(profile: int = asusd_mod.QUIET):
    fw = fw_mod.FansWindow.__new__(fw_mod.FansWindow)
    fw._loading = False
    fw._refresh_pending = False
    fw._profile = profile
    fw._fan_profile = asusd_mod.fan_profile_for(profile)
    fw._attrs = {}
    fw._ppt_values = {}
    fw._ppt_defaults = {}
    fw._curves = {}
    fw._enabled = {}
    fw._other_curves = {}
    fw._fans_dirty = False

    fw.asusd = mock.Mock()
    fw.window = mock.Mock()
    fw.window.profiles = [
        asusd_mod.QUIET,
        asusd_mod.BALANCED,
        asusd_mod.PERFORMANCE,
    ]
    run_sync_window_tasks(fw)

    fw.ppt_status = make_label()
    fw.fan_status = make_label()
    fw.fan_title = make_label()
    fw.boost = mock.Mock()

    fw.platform_slider = FakeSlider()
    fw.cpu_slider = FakeSlider()
    fw.fppt_slider = FakeSlider()
    fw.fppt_slider.set_visible(False)
    fw._ppt_sliders = {
        "platform": fw.platform_slider,
        "cpu": fw.cpu_slider,
        "fppt": fw.fppt_slider,
    }

    fw.cpu_chart = FakeChart()
    fw.gpu_chart = FakeChart()
    fw.mid_chart = FakeChart(visible=False)
    fw._auto_power = SimpleNamespace(get_active=lambda: False)
    fw._auto_fans = SimpleNamespace(get_active=lambda: False)
    return fw


def sample_curve(base_pwm: int = 20, *, enabled: bool = False) -> dict:
    return {
        "pwm": [base_pwm + i * 20 for i in range(8)],
        "temp": [40, 50, 60, 65, 70, 75, 80, 90],
        "enabled": enabled,
    }


def ppt_attr(current: int, minimum: int, maximum: int, default: int) -> dict:
    return {
        "CurrentValue": current,
        "MinValue": minimum,
        "MaxValue": maximum,
        "DefaultValue": default,
    }


class FansWindowTests(unittest.TestCase):
    def test_collect_reads_active_profile_epp_not_last_profile(self) -> None:
        fw = make_fans_window(profile=asusd_mod.QUIET)
        fw.asusd.platform_profile.return_value = asusd_mod.QUIET
        fw.asusd.fan_curves.return_value = {
            "CPU": sample_curve(20),
            "GPU": sample_curve(30),
        }
        fw.asusd.armoury.side_effect = asusd_mod.AsusdError("absent")
        fw.asusd.profile_epp.return_value = 4

        data = fw._collect()

        fw.asusd.profile_epp.assert_called_once_with(asusd_mod.QUIET)
        self.assertEqual(data["profile"], asusd_mod.QUIET)
        self.assertEqual(data["epp"], 4)

    def test_fan_status_info_states(self) -> None:
        self.assertEqual(fw_mod.fan_status_info({}, {}, False), ("Not available", False))
        curves = {"CPU": sample_curve(20), "GPU": sample_curve(30)}
        self.assertEqual(
            fw_mod.fan_status_info(curves, {"CPU": False, "GPU": False}, False),
            ("BIOS default", None),
        )
        self.assertEqual(
            fw_mod.fan_status_info(curves, {"CPU": False, "GPU": True}, False),
            ("Custom active", True),
        )
        self.assertEqual(
            fw_mod.fan_status_info(curves, {"CPU": True, "GPU": True}, True),
            ("Unsaved changes", False),
        )

    def test_interpolate_pwm_and_format_live_reading(self) -> None:
        temps = [40, 60, 80]
        pwm = [0, 100, 200]
        self.assertIsNone(chart_mod.interpolate_pwm(50, [], pwm))
        self.assertIsNone(chart_mod.interpolate_pwm(50, [40], pwm))
        self.assertEqual(chart_mod.interpolate_pwm(30, temps, pwm), 0.0)
        self.assertEqual(chart_mod.interpolate_pwm(40, temps, pwm), 0.0)
        self.assertEqual(chart_mod.interpolate_pwm(50, temps, pwm), 50.0)
        self.assertEqual(chart_mod.interpolate_pwm(95, temps, pwm), 200.0)

        self.assertEqual(chart_mod.format_live_reading(54.2, 1800), "54°C · 1800 RPM")
        self.assertEqual(chart_mod.format_live_reading(54.2, None), "54°C")
        self.assertEqual(chart_mod.format_live_reading(None, 2200), "2200 RPM")
        self.assertEqual(chart_mod.format_live_reading(54.0, None, pwm=128), "54°C · 50%")
        self.assertEqual(chart_mod.format_live_reading(None, None), "")

    def test_monitor_fans_reads_optional_third_mid_fan(self) -> None:
        with tempfile.TemporaryDirectory() as two_fan:
            Path(two_fan, "fan1_label").write_text("cpu_fan\n", encoding="utf-8")
            Path(two_fan, "fan1_input").write_text("2100\n", encoding="utf-8")
            Path(two_fan, "fan2_label").write_text("gpu_fan\n", encoding="utf-8")
            Path(two_fan, "fan2_input").write_text("2300\n", encoding="utf-8")

            mon2 = object.__new__(system.Monitor)
            mon2._hwmon = {"asus": [two_fan]}
            fans2 = mon2.fans()
            self.assertEqual(fans2["cpu"], 2100)
            self.assertEqual(fans2["gpu"], 2300)
            self.assertIsNone(fans2["mid"])
            self.assertIsNone(fans2["mid_pwm"])

        with tempfile.TemporaryDirectory() as three_fan:
            Path(three_fan, "fan1_label").write_text("cpu_fan\n", encoding="utf-8")
            Path(three_fan, "fan1_input").write_text("2100\n", encoding="utf-8")
            Path(three_fan, "fan2_label").write_text("gpu_fan\n", encoding="utf-8")
            Path(three_fan, "fan2_input").write_text("2300\n", encoding="utf-8")
            Path(three_fan, "fan3_label").write_text("mid_fan\n", encoding="utf-8")
            Path(three_fan, "fan3_input").write_text("1500\n", encoding="utf-8")
            Path(three_fan, "pwm3").write_text("90\n", encoding="utf-8")

            mon3 = object.__new__(system.Monitor)
            mon3._hwmon = {"asus": [three_fan]}
            fans3 = mon3.fans()
            self.assertEqual(fans3["mid"], 1500)
            self.assertEqual(fans3["mid_pwm"], 90)

    def test_fans_window_two_vs_three_fan_charts_and_apply(self) -> None:
        fw = make_fans_window()
        fw._curves = {"CPU": sample_curve(20), "GPU": sample_curve(30)}
        fw._apply_curves()
        self.assertFalse(fw.mid_chart.get_visible())
        self.assertEqual(fw.cpu_chart.size_requests[-1], (-1, 185))
        self.assertEqual(sorted(fw._chart_curves()), ["CPU", "GPU"])

        with mock.patch.object(fw, "refresh"):
            fw.apply_fan_curve()
        written_two = [c.args[1] for c in fw.asusd.set_fan_curve.call_args_list]
        self.assertEqual(written_two, ["CPU", "GPU"])

        fw.asusd.reset_mock()
        fw._curves = {
            "CPU": sample_curve(20, enabled=True),
            "GPU": sample_curve(30, enabled=True),
            "MID": sample_curve(10, enabled=True),
        }
        fw._apply_curves()
        fw._update_fan_status()
        self.assertTrue(fw.mid_chart.get_visible())
        self.assertEqual(fw.cpu_chart.size_requests[-1], (-1, 145))
        self.assertEqual(fw.mid_chart.size_requests[-1], (-1, 145))
        self.assertEqual(sorted(fw._chart_curves()), ["CPU", "GPU", "MID"])
        self.assertEqual(fw.fan_status.text, "Custom active")

        with mock.patch.object(fw, "refresh"):
            fw.apply_fan_curve()
        written_three = [c.args[1] for c in fw.asusd.set_fan_curve.call_args_list]
        self.assertEqual(written_three, ["CPU", "GPU", "MID"])

    def test_fans_window_two_vs_three_ppt_sliders(self) -> None:
        fw = make_fans_window()
        fw._attrs = {
            asusd_mod.ATTR_PPT_PLATFORM: ppt_attr(60, 40, 125, 115),
            asusd_mod.ATTR_PPT_APU: ppt_attr(35, 25, 80, 80),
        }
        fw._apply_ppt()
        self.assertFalse(fw.fppt_slider.get_visible())
        self.assertEqual(sorted(fw._ppt_values), ["cpu", "platform"])

        fw._attrs[asusd_mod.ATTR_PPT_FPPT] = ppt_attr(75, 45, 125, 90)
        fw._apply_ppt()
        self.assertTrue(fw.fppt_slider.get_visible())
        self.assertTrue(fw.fppt_slider.get_sensitive())
        self.assertEqual(fw._ppt_values["fppt"], 75)

        fw.fppt_slider.set_value(85)
        fw._on_ppt_slider("fppt", 85.0)
        self.assertEqual(fw.ppt_status.text, "Not applied")

        with mock.patch.object(fw, "refresh"):
            fw.apply_power_limits()
        fw.asusd.set_armoury.assert_any_call(asusd_mod.ATTR_PPT_FPPT, 85)

        fw.asusd.reset_mock()
        with mock.patch.object(fw, "refresh"):
            fw.factory_defaults()
        fw.asusd.set_armoury.assert_any_call(asusd_mod.ATTR_PPT_FPPT, 90)

        # Removing fPPT later hides the slider and clears stale values.
        del fw._attrs[asusd_mod.ATTR_PPT_FPPT]
        fw._apply_ppt()
        self.assertFalse(fw.fppt_slider.get_visible())
        self.assertNotIn("fppt", fw._ppt_values)
        self.assertNotIn("fppt", fw._ppt_defaults)

    def test_update_live_forwards_dgpu_cpu_and_optional_mid(self) -> None:
        fw = make_fans_window()
        snapshot = {
            "cpu_temp": 58.4,
            "gpu": {"igpu": 49.0, "dgpu": None},
            "fans": {
                "cpu": 2100,
                "gpu": 0,
                "mid": 1400,
                "cpu_pwm": 80,
                "gpu_pwm": 0,
                "mid_pwm": 60,
            },
        }
        fw.update_live(snapshot)
        self.assertEqual(fw.cpu_chart.live_calls[-1], (58.4, 2100, 80))
        self.assertEqual(fw.gpu_chart.live_calls[-1], (None, 0, 0))
        self.assertEqual(fw.mid_chart.live_calls, [])

        fw.mid_chart.set_visible(True)
        snapshot["gpu"] = {"igpu": 49.0, "dgpu": 67.2}
        fw.update_live(snapshot)
        self.assertEqual(fw.gpu_chart.live_calls[-1], (67.2, 0, 0))
        self.assertEqual(fw.mid_chart.live_calls[-1], (None, 1400, 60))

    def test_vertical_slider_programmatic_range_clamp_never_commits(self) -> None:
        commits: list[float] = []
        slider = widgets_mod.VerticalSlider.__new__(widgets_mod.VerticalSlider)
        slider._on_change = commits.append
        slider._unit = "W"
        slider._timeout = 0
        slider._blocked = False
        slider._value_label = make_label()
        slider.scale = FakeScale(0.0, 100.0, 50.0, slider._changed)
        slider._rebuild_marks = mock.Mock()

        with mock.patch.object(widgets_mod.GLib, "timeout_add") as timeout_add:
            slider.set_range(25.0, 45.0)
        timeout_add.assert_not_called()
        self.assertFalse(slider._blocked)
        self.assertEqual(slider.scale.value, 45.0)
        self.assertEqual(slider._value_label.text, "45W")

        # Programmatic set_value cancels an active debounce timer and blocks commit.
        slider._timeout = 99
        with (
            mock.patch.object(widgets_mod.GLib, "source_remove") as source_remove,
            mock.patch.object(widgets_mod.GLib, "timeout_add") as timeout_add,
        ):
            slider.set_value(35.0)
        source_remove.assert_called_once_with(99)
        timeout_add.assert_not_called()
        self.assertEqual(slider._timeout, 0)
        self.assertEqual(slider._value_label.text, "35W")
        self.assertEqual(commits, [])

    def test_fan_chart_set_live_rounds_and_deduplicates(self) -> None:
        widget = chart_mod.FanChart.__new__(chart_mod.FanChart)
        widget._live_temp = None
        widget._live_rpm = None
        widget._live_pwm = None
        widget.queue_draw = mock.Mock()

        widget.set_live(54.23, 1800, None)
        widget.set_live(54.24, 1800, None)
        self.assertEqual(widget._live_temp, 54.2)
        self.assertEqual(widget._live_rpm, 1800)
        widget.queue_draw.assert_called_once()

        widget.set_live(54.3, 1800, None)
        self.assertEqual(widget.queue_draw.call_count, 2)


if __name__ == "__main__":
    unittest.main()
