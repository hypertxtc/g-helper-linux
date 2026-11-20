# SPDX-License-Identifier: GPL-3.0-or-later

"""The G-Helper "Fans and Power" window."""

from __future__ import annotations

import logging

from ..backend import asusd as asusd_mod
from ..backend import fan_presets
from ..deps import Adw, Gio, GLib, Gtk
from .chart import CPU_COLOR, GPU_COLOR, FanChart
from .icons import ThemedIcon
from .profiles import PROFILE_LABELS
from .theme import register_root
from .widgets import SectionHeader, VerticalSlider, flat_button, dropdown

log = logging.getLogger(__name__)

EPP_VALUES = [4, 3, 2, 1]
EPP_LABELS = ["Power", "Balance power", "Balance performance", "Performance"]


def _cancelled(error: GLib.Error) -> bool:
    """True when a file dialog was dismissed rather than failing."""
    try:
        dismissed = int(Gtk.DialogError.DISMISSED)
    except AttributeError:
        dismissed = 1
    return error.code == dismissed and "dialog-error" in str(error.domain)


class FansWindow(Adw.Window):
    def __init__(self, window) -> None:
        super().__init__(
            transient_for=window,
            modal=False,
            title="Fans and Power",
            default_width=760,
            default_height=620,
        )
        self.window = window
        self.asusd = window.asusd
        self.settings = window.settings
        self._loading = False
        self._refresh_pending = False
        self._profile = window.current_profile
        self._fan_profile = asusd_mod.fan_profile_for(self._profile)
        self._attrs: dict[str, dict] = {}
        self._ppt_values: dict[str, int] = {}
        self._ppt_defaults: dict[str, int] = {}
        self._curves: dict[str, dict] = {}
        self._enabled: dict[str, bool] = {}
        self._other_curves: dict[int, dict[str, dict]] = {}
        self._curves_popover: Gtk.Popover | None = None

        register_root(self)

        header = Adw.HeaderBar()
        header.add_css_class("gh-header")
        title = Gtk.Label(label="Fans and Power")
        title.add_css_class("gh-app-title")
        header.pack_start(title)
        header.set_title_widget(Gtk.Label(label=""))

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(10)
        content.set_margin_end(10)

        content.append(self._build_left())
        content.append(self._build_right())

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(content)
        self.set_content(toolbar)

        manager = Adw.StyleManager.get_default()
        self._dark_handler = manager.connect("notify::dark", self._on_dark)
        self.connect("destroy", self._on_destroy)
        self._on_dark()
        self.refresh()

    # -- construction ----------------------------------------------------
    def _build_left(self) -> Gtk.Widget:
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_size_request(210, -1)

        self.ppt_header = SectionHeader("gh-power-symbolic", "Power Limits (PPT)")
        self.ppt_status = Gtk.Label(label="\u2014", xalign=0.0)
        self.ppt_status.add_css_class("gh-status")

        sliders = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        sliders.set_halign(Gtk.Align.CENTER)
        sliders.set_homogeneous(True)
        sliders.set_vexpand(True)
        self.platform_slider = VerticalSlider(
            "Platform", 0, 100, 50, lambda value: self._on_ppt_slider("platform", value)
        )
        self.cpu_slider = VerticalSlider(
            "CPU", 0, 100, 50, lambda value: self._on_ppt_slider("cpu", value)
        )
        sliders.append(self.platform_slider)
        sliders.append(self.cpu_slider)

        left.append(self.ppt_header)
        left.append(self.ppt_status)
        left.append(sliders)

        self._auto_power = Gtk.CheckButton(label="Auto apply power limits")
        self._auto_power.add_css_class("gh-check")
        self._auto_power.set_active(bool(self.settings.get("auto_apply_ppt")))
        self._auto_power.connect("toggled", self._on_auto_power)
        left.append(self._auto_power)

        apply_power = flat_button("Apply Power Limits")
        apply_power.set_hexpand(True)
        apply_power.connect("clicked", lambda *_: self.apply_power_limits())
        left.append(apply_power)
        return left

    def _build_right(self) -> Gtk.Widget:
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        right.set_hexpand(True)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = ThemedIcon("gh-fan-symbolic", 16)
        icon.add_css_class("gh-section-icon")
        self.fan_title = Gtk.Label(label="Fan Profiles", xalign=0.0)
        self.fan_title.add_css_class("gh-section-title")
        top.append(icon)
        top.append(self.fan_title)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top.append(spacer)
        boost_label = Gtk.Label(label="CPU Boost")
        boost_label.add_css_class("gh-live")
        top.append(boost_label)
        self.boost = dropdown(EPP_LABELS)
        self.boost.set_size_request(170, -1)
        self.boost.set_hexpand(False)
        self.boost.connect("notify::selected", self._on_boost_changed)
        top.append(self.boost)
        right.append(top)

        self.cpu_chart = FanChart("CPU Fan Profile", CPU_COLOR)
        self.gpu_chart = FanChart("GPU Fan Profile", GPU_COLOR)
        self.cpu_chart.connect("changed", self._on_fan_changed)
        self.gpu_chart.connect("changed", self._on_fan_changed)
        self.cpu_chart.set_size_request(-1, 185)
        self.gpu_chart.set_size_request(-1, 185)
        right.append(self.cpu_chart)
        right.append(self.gpu_chart)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._auto_fans = Gtk.CheckButton(label="Auto apply fan curves")
        self._auto_fans.add_css_class("gh-check")
        self._auto_fans.set_active(bool(self.settings.get("auto_apply_fans")))
        self._auto_fans.connect("toggled", self._on_auto_fans)
        bottom.append(self._auto_fans)
        stretch = Gtk.Box()
        stretch.set_hexpand(True)
        bottom.append(stretch)

        defaults = flat_button("Factory Defaults")
        defaults.connect("clicked", lambda *_: self.factory_defaults())
        bottom.append(defaults)
        bottom.append(self._build_curves_button())
        apply_fans = flat_button("Apply Fan Curve")
        apply_fans.connect("clicked", lambda *_: self.apply_fan_curve())
        bottom.append(apply_fans)
        right.append(bottom)
        return right

    def _build_curves_button(self) -> Gtk.MenuButton:
        button = Gtk.MenuButton(label="Curves\u2026")
        button.add_css_class("gh-button")
        button.set_tooltip_text("Copy a curve, save or load presets, import / export")
        popover = Gtk.Popover()
        popover.add_css_class("gh-popover")
        popover.set_has_arrow(False)
        popover.connect("notify::visible", self._on_curves_visible)
        button.set_popover(popover)
        self._curves_popover = popover
        return button

    def _on_curves_visible(self, popover: Gtk.Popover, _param) -> None:
        if popover.get_visible():
            self._fill_curves_popover(popover)

    def _fill_curves_popover(self, popover: Gtk.Popover) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        others = self._copy_candidates()
        if others:
            box.append(self._popover_heading("Copy from"))
            for fan_profile, label in others:
                button = self._popover_button(label)
                button.connect(
                    "clicked", lambda _b, p=fan_profile: self.copy_from(p, popover)
                )
                box.append(button)

        presets = fan_presets.load()
        box.append(self._popover_heading("Saved presets"))
        if not presets:
            empty = Gtk.Label(label="None yet", xalign=0.0)
            empty.add_css_class("gh-live")
            empty.set_margin_start(8)
            empty.set_margin_bottom(2)
            box.append(empty)
        for name in sorted(presets):
            box.append(self._preset_row(name, presets[name], popover))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        actions = (
            ("Save current as preset\u2026", lambda *_: self.save_preset(popover)),
            ("Import from file\u2026", lambda *_: self.import_curves(popover)),
            ("Export current curve\u2026", lambda *_: self.export_curves()),
        )
        for label, callback in actions:
            button = self._popover_button(label)
            button.connect("clicked", callback)
            box.append(button)
        popover.set_child(box)

    def _preset_row(
        self, name: str, preset: dict, popover: Gtk.Popover
    ) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        button = self._popover_button(name)
        button.set_tooltip_text(
            f"{fan_presets.summary(preset.get('curves') or {})}\n"
            f"Saved {preset.get('created') or 'earlier'}"
        )
        button.set_hexpand(True)
        button.connect("clicked", lambda *_: self.load_preset(name, popover))
        remove = Gtk.Button(label="\u00d7")
        remove.add_css_class("gh-flat-icon")
        remove.set_tooltip_text(f"Delete preset \u201c{name}\u201d")
        remove.set_valign(Gtk.Align.CENTER)
        remove.connect("clicked", lambda *_: self.delete_preset(name, popover))
        row.append(button)
        row.append(remove)
        return row

    @staticmethod
    def _popover_heading(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0.0)
        label.add_css_class("gh-live")
        label.set_margin_start(8)
        label.set_margin_top(4)
        return label

    @staticmethod
    def _popover_button(label: str) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("gh-popover-item")
        text = Gtk.Label(label=label, xalign=0.0)
        button.set_child(text)
        return button

    def _copy_candidates(self) -> list[tuple[int, str]]:
        current = self._fan_profile
        seen: list[tuple[int, str]] = []
        done: set[int] = set()
        for profile in self.window.profiles:
            fan_profile = asusd_mod.fan_profile_for(profile)
            if fan_profile == current or fan_profile in done:
                continue
            if fan_profile not in self._other_curves:
                continue
            done.add(fan_profile)
            seen.append((fan_profile, PROFILE_LABELS.get(profile, str(profile))))
        return seen

    # -- data ------------------------------------------------------------
    def refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.window.run(self._collect, done=self._apply, error=self._load_failed)

    def _collect(self) -> dict:
        profile = self.asusd.platform_profile()
        fan_profile = asusd_mod.fan_profile_for(profile)
        data: dict = {
            "profile": profile,
            "fan_profile": fan_profile,
            "curves": {},
            "attrs": {},
            "epp": None,
            "error": None,
        }
        try:
            data["curves"] = self.asusd.fan_curves(fan_profile)
        except asusd_mod.AsusdError as exc:
            data["error"] = str(exc)
        data["other_curves"] = {}
        for profile in self.window.profiles:
            other = asusd_mod.fan_profile_for(profile)
            if other == fan_profile or other in data["other_curves"]:
                continue
            try:
                data["other_curves"][other] = self.asusd.fan_curves(other)
            except asusd_mod.AsusdError:
                continue
        for logical in (
            asusd_mod.ATTR_PPT_PLATFORM,
            asusd_mod.ATTR_PPT_APU,
            asusd_mod.ATTR_PPT_FPPT,
        ):
            try:
                data["attrs"][logical] = self.asusd.armoury(logical)
            except asusd_mod.AsusdError:
                continue
        try:
            data["epp"] = self.asusd.profile_epp(profile)
        except asusd_mod.AsusdError:
            data["epp"] = None
        return data

    def _load_failed(self, exc: Exception) -> None:
        self._refresh_pending = False
        self.window.show_error(exc, quiet=True)

    def _apply(self, data: dict) -> None:
        self._refresh_pending = False
        self._profile = int(data["profile"])
        self._fan_profile = int(data["fan_profile"])
        self._attrs = data.get("attrs") or {}
        self._curves = data.get("curves") or {}
        self._other_curves = data.get("other_curves") or {}
        self.fan_title.set_text(
            f"Fan Profiles: {PROFILE_LABELS.get(self._profile, 'Profile')}"
        )

        self._apply_ppt()
        self._apply_epp(data.get("epp"))
        self._apply_curves()

    def _apply_ppt(self) -> None:
        pairs = (
            ("platform", self.platform_slider, asusd_mod.ATTR_PPT_PLATFORM),
            ("cpu", self.cpu_slider, asusd_mod.ATTR_PPT_APU),
        )
        present = False
        for key, slider, logical in pairs:
            attr = self._attrs.get(logical)
            if attr is None:
                slider.set_sensitive(False)
                continue
            current = attr.get("CurrentValue")
            minimum = attr.get("MinValue")
            maximum = attr.get("MaxValue")
            if current is None or minimum is None or maximum is None:
                slider.set_sensitive(False)
                continue
            if maximum <= minimum:
                slider.set_sensitive(False)
                continue
            present = True
            slider.set_sensitive(True)
            slider.set_range(float(minimum), float(maximum))
            slider.set_value(float(current))
            self._ppt_values[key] = int(current)
            default = attr.get("DefaultValue")
            if default is not None and int(default) > 0:
                self._ppt_defaults[key] = int(default)

        if not present:
            self._set_ppt_status("Not available", ok=False)
            return
        self._update_ppt_status()

    def _update_ppt_status(self) -> None:
        applied = True
        present = False
        for key, logical in (
            ("platform", asusd_mod.ATTR_PPT_PLATFORM),
            ("cpu", asusd_mod.ATTR_PPT_APU),
        ):
            attr = self._attrs.get(logical)
            if attr is None or key not in self._ppt_values:
                continue
            present = True
            current = int(attr.get("CurrentValue", -1000))
            if abs(current - self._ppt_values[key]) > 1:
                applied = False
        if not present:
            self._set_ppt_status("Not available", ok=False)
            return
        self._set_ppt_status("Applied" if applied else "Not applied", ok=applied)

    def _set_ppt_status(self, text: str, *, ok: bool | None = None) -> None:
        self.ppt_status.set_text(text)
        for css in ("gh-status-ok", "gh-status-error"):
            self.ppt_status.remove_css_class(css)
        if ok is True:
            self.ppt_status.add_css_class("gh-status-ok")
        elif ok is False:
            self.ppt_status.add_css_class("gh-status-error")

    def _apply_epp(self, epp: int | None) -> None:
        self._loading = True
        try:
            if epp is None:
                self.boost.set_sensitive(False)
                return
            self.boost.set_sensitive(True)
            if int(epp) in EPP_VALUES:
                self.boost.set_selected(EPP_VALUES.index(int(epp)))
        finally:
            self._loading = False

    def _apply_curves(self) -> None:
        for name, chart in (("CPU", self.cpu_chart), ("GPU", self.gpu_chart)):
            curve = self._curves.get(name)
            if curve is None:
                chart.set_sensitive(False)
                continue
            chart.set_sensitive(True)
            chart.set_points(curve.get("pwm", []), curve.get("temp", []))
            self._enabled[name] = bool(curve.get("enabled", False))

    # -- power limits -----------------------------------------------------
    def _on_ppt_slider(self, key: str, value: float) -> None:
        if self._loading:
            return
        self._ppt_values[key] = int(round(value))
        self._update_ppt_status()
        if self._auto_power.get_active():
            self.apply_power_limits()

    def _on_auto_power(self, button: Gtk.CheckButton) -> None:
        self.settings.set("auto_apply_ppt", button.get_active())

    def apply_power_limits(self) -> None:
        values = dict(self._ppt_values)

        def work() -> None:
            self.asusd.set_enable_ppt_group(True)
            for key, logical in (
                ("platform", asusd_mod.ATTR_PPT_PLATFORM),
                ("cpu", asusd_mod.ATTR_PPT_APU),
            ):
                if logical in self._attrs and key in values:
                    self.asusd.set_armoury(logical, values[key])

        def done(_result) -> None:
            self.window.toast("Power limits applied")
            self.refresh()

        self.window.run(work, done=done, error=self._failed)

    # -- fans -------------------------------------------------------------
    def _on_fan_changed(self, _chart) -> None:
        if self._auto_fans.get_active():
            self.apply_fan_curve()

    def _on_auto_fans(self, button: Gtk.CheckButton) -> None:
        self.settings.set("auto_apply_fans", button.get_active())

    def apply_fan_curve(self) -> None:
        cpu_pwm, cpu_temp = self.cpu_chart.points()
        gpu_pwm, gpu_temp = self.gpu_chart.points()
        fan_profile = self._fan_profile

        def work() -> None:
            self.asusd.set_fan_curve(fan_profile, "CPU", cpu_pwm, cpu_temp, True)
            self.asusd.set_fan_curve(fan_profile, "GPU", gpu_pwm, gpu_temp, True)
            self.asusd.set_fan_curves_enabled(fan_profile, True)

        def done(_result) -> None:
            self.window.toast("Fan curve applied")
            self.refresh()

        self.window.run(work, done=done, error=self._failed)

    def factory_defaults(self) -> None:
        fan_profile = self._fan_profile
        defaults = dict(self._ppt_defaults)

        def work() -> None:
            self.asusd.reset_fan_curves(fan_profile)
            for key, logical in (
                ("platform", asusd_mod.ATTR_PPT_PLATFORM),
                ("cpu", asusd_mod.ATTR_PPT_APU),
            ):
                if logical in self._attrs and key in defaults:
                    self.asusd.set_armoury(logical, defaults[key])

        def done(_result) -> None:
            self.window.toast("Restored factory defaults")
            self.refresh()

        self.window.run(work, done=done, error=self._failed)

    # -- presets ----------------------------------------------------------
    def _chart_curves(self) -> dict[str, dict]:
        cpu_pwm, cpu_temp = self.cpu_chart.points()
        gpu_pwm, gpu_temp = self.gpu_chart.points()
        return {
            "CPU": {"pwm": cpu_pwm, "temp": cpu_temp, "enabled": True},
            "GPU": {"pwm": gpu_pwm, "temp": gpu_temp, "enabled": True},
        }

    def _load_curves(self, curves: dict, source: str) -> None:
        loaded = False
        for fan, chart in (("CPU", self.cpu_chart), ("GPU", self.gpu_chart)):
            curve = curves.get(fan)
            if not curve:
                continue
            chart.set_points(curve.get("pwm") or [], curve.get("temp") or [])
            loaded = True
        if not loaded:
            self.window.toast("That curve has no usable points")
            return
        if self._auto_fans.get_active():
            self.apply_fan_curve()
        else:
            self.window.toast(f"Loaded {source} \u2014 press Apply Fan Curve")

    def copy_from(self, fan_profile: int, popover: Gtk.Popover | None = None) -> None:
        if popover is not None:
            popover.popdown()
        curves = self._other_curves.get(fan_profile)
        if not curves:
            self.window.toast("That profile has no fan curve")
            return
        label = next(
            (name for pid, name in self._copy_candidates() if pid == fan_profile),
            "another profile",
        )
        self._load_curves(curves, f"the {label} fan curve")

    def load_preset(self, name: str, popover: Gtk.Popover | None = None) -> None:
        if popover is not None:
            popover.popdown()
        preset = fan_presets.load().get(name)
        if preset is None:
            self.window.toast(f"Preset \u201c{name}\u201d disappeared")
            return
        self._load_curves(preset.get("curves") or {}, f"preset \u201c{name}\u201d")

    def delete_preset(self, name: str, popover: Gtk.Popover | None = None) -> None:
        try:
            removed = fan_presets.remove(name)
        except (fan_presets.CurveError, OSError) as exc:
            self.window.show_error(exc)
            return
        if removed:
            self.window.toast(f"Deleted preset \u201c{name}\u201d")
        if popover is not None:
            self._fill_curves_popover(popover)

    def _charts_available(self) -> bool:
        if not self.cpu_chart.get_sensitive() or not self.gpu_chart.get_sensitive():
            self.window.toast("Fan curves are not available on this system")
            return False
        return True

    def save_preset(self, popover: Gtk.Popover | None = None) -> None:
        if popover is not None:
            popover.popdown()
        if not self._charts_available():
            return
        curves = self._chart_curves()
        label = PROFILE_LABELS.get(self._profile, "Custom")
        dialog = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading="Save fan curve preset",
            body=(
                "The CPU and GPU curves are stored in fan-curves.json in the "
                "G-Helper config folder."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        entry = Gtk.Entry()
        entry.set_text(f"{label} curve")
        entry.set_activates_default(True)
        entry.select_region(0, -1)
        dialog.set_extra_child(entry)
        dialog.connect("response", self._on_save_preset, entry, curves)
        dialog.present()

    def _on_save_preset(
        self, _dialog, response: str, entry: Gtk.Entry, curves: dict
    ) -> None:
        if response != "save":
            return
        try:
            name, _entry = fan_presets.add(entry.get_text(), curves)
        except (fan_presets.CurveError, OSError) as exc:
            self.window.show_error(exc)
            return
        self.window.toast(f"Saved preset \u201c{name}\u201d")

    def import_curves(self, popover: Gtk.Popover | None = None) -> None:
        if popover is not None:
            popover.popdown()
        dialog = Gtk.FileDialog(title="Import fan curve preset")
        dialog.set_filters(self._json_filters())
        dialog.open(self, None, self._on_import_file)

    def _on_import_file(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error as exc:
            if not _cancelled(exc):
                self.window.show_error(exc)
            return
        if file is None:
            return
        try:
            names = fan_presets.import_file(file.get_path())
        except (fan_presets.CurveError, OSError) as exc:
            self.window.show_error(exc)
            return
        if len(names) == 1:
            self.window.toast(f"Imported preset \u201c{names[0]}\u201d")
        else:
            self.window.toast(f"Imported {len(names)} presets")

    def export_curves(self) -> None:
        if not self._charts_available():
            return
        label = PROFILE_LABELS.get(self._profile, "custom").lower()
        dialog = Gtk.FileDialog(
            title="Export fan curve preset",
            initial_name=f"ghelper-fan-{label}.json",
        )
        dialog.set_filters(self._json_filters())
        dialog.save(self, None, self._on_export_file)

    def _on_export_file(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.save_finish(result)
        except GLib.Error as exc:
            if not _cancelled(exc):
                self.window.show_error(exc)
            return
        if file is None:
            return
        label = PROFILE_LABELS.get(self._profile, "Custom")
        try:
            name = fan_presets.export_file(
                file.get_path(), f"{label} curve", self._chart_curves()
            )
        except (fan_presets.CurveError, OSError) as exc:
            self.window.show_error(exc)
            return
        self.window.toast(f"Exported \u201c{name}\u201d")

    @staticmethod
    def _json_filters() -> Gio.ListStore:
        filters = Gio.ListStore.new(Gtk.FileFilter)
        json_filter = Gtk.FileFilter()
        json_filter.set_name("Fan curve presets (*.json)")
        json_filter.add_pattern("*.json")
        filters.append(json_filter)
        return filters

    # -- CPU boost --------------------------------------------------------
    def _on_boost_changed(self, row: Gtk.DropDown, _param) -> None:
        if self._loading:
            return
        index = row.get_selected()
        if not (0 <= index < len(EPP_VALUES)):
            return
        value = EPP_VALUES[index]
        profile = self._profile
        self.window.run(
            lambda: self.asusd.set_profile_epp(profile, value),
            done=lambda _r: self.window.toast(f"CPU boost: {EPP_LABELS[index]}"),
            error=self._failed,
        )

    # -- misc -------------------------------------------------------------
    def _failed(self, exc: Exception) -> None:
        self.window.show_error(exc)
        self.refresh()

    def _on_dark(self, *_args) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        if hasattr(self, "cpu_chart"):
            self.cpu_chart.set_dark(dark)
            self.gpu_chart.set_dark(dark)

    def _on_destroy(self, *_args) -> None:
        try:
            Adw.StyleManager.get_default().disconnect(self._dark_handler)
        except (TypeError, ValueError):
            pass
        self.window.forget_fans_window(self)
