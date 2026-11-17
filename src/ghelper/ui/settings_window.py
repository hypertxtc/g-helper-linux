# SPDX-License-Identifier: GPL-3.0-or-later

"""Settings window for controls that G-Helper keeps off the main page."""

from __future__ import annotations

from .. import APP_VERSION
from ..backend import asusd as asusd_mod
from ..backend import autostart
from ..backend import cardwire as cw
from ..backend import hotkeys as hotkeys_mod
from ..backend.system import machine_name
from ..deps import Adw, Gtk
from .theme import register_root
from .widgets import Section, ToggleRow, ValueRow, dropdown, flat_button

PROFILE_LABELS = {
    asusd_mod.QUIET: "Silent",
    asusd_mod.LOW_POWER: "Silent",
    asusd_mod.BALANCED: "Balanced",
    asusd_mod.PERFORMANCE: "Turbo",
    asusd_mod.CUSTOM: "Custom",
}


class SettingsWindow(Adw.Window):
    def __init__(self, window) -> None:
        super().__init__(
            transient_for=window,
            modal=False,
            title="Settings",
            default_width=470,
            default_height=680,
        )
        self.window = window
        self.asusd = window.asusd
        self._loading = False
        self._refresh_pending = False
        self.connect("destroy", self._on_destroy)

        register_root(self)

        header = Adw.HeaderBar()
        header.add_css_class("gh-header")
        title = Gtk.Label(label="Settings")
        title.add_css_class("gh-app-title")
        header.pack_start(title)
        header.set_title_widget(Gtk.Label(label=""))
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_margin_top(10)
        body.set_margin_bottom(12)
        body.set_margin_start(10)
        body.set_margin_end(10)

        self._build_power(body)
        self._build_cpu(body)
        self._build_gpu(body)
        self._build_hotkeys(body)
        self._build_window(body)
        self._build_about(body)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("gh-scroll")
        scrolled.set_child(body)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scrolled)
        self.set_content(toolbar)

        self.refresh()

    # -- sections ---------------------------------------------------------
    def _build_power(self, body: Gtk.Box) -> None:
        section = Section("gh-optimized-symbolic", "Power Source")
        self._auto_switch = ToggleRow(
            "Switch profile with power source",
            subtitle="asusd applies these profiles automatically.",
        )
        self._auto_switch.switch.connect("notify::active", self._on_auto_toggled)
        section.add(self._auto_switch)

        self._ac_combo = self._profile_dropdown()
        self._battery_combo = self._profile_dropdown()
        self._ac_combo.connect("notify::selected", self._on_ac_selected)
        self._battery_combo.connect("notify::selected", self._on_battery_selected)
        section.add(self._labeled("On AC power", self._ac_combo))
        section.add(self._labeled("On battery", self._battery_combo))
        body.append(section)

    def _build_cpu(self, body: Gtk.Box) -> None:
        section = Section("gh-balanced-symbolic", "CPU")
        self._epp_switch = ToggleRow(
            "Link energy preference to profile",
            subtitle="Quiet uses power saving, Turbo uses performance.",
        )
        self._epp_switch.switch.connect("notify::active", self._on_epp_toggled)
        section.add(self._epp_switch)
        self._epp_value = ValueRow("Current EPP", "—")
        section.add(self._epp_value)
        body.append(section)

    def _build_gpu(self, body: Gtk.Box) -> None:
        section = Section("gh-chip-symbolic", "Firmware GPU")
        self._dgpu_switch = ToggleRow(
            "Disable discrete GPU (ECO)",
            subtitle="Applied after a reboot.",
        )
        self._dgpu_switch.switch.connect("notify::active", self._on_dgpu_toggled)
        section.add(self._dgpu_switch)

        self._mux_combo = dropdown(
            ["Ultimate — dGPU drives the panel", "Optimus — hybrid display"]
        )
        self._mux_combo.connect("notify::selected", self._on_mux_selected)
        section.add(self._labeled("GPU MUX", self._mux_combo))

        self._pending_value = ValueRow("Queued changes", "—")
        section.add(self._pending_value)
        apply = flat_button("Apply queued changes", small=True)
        apply.connect("clicked", lambda *_: self._apply_queued())
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        row.append(apply)
        section.add(row)
        body.append(section)

    def _build_hotkeys(self, body: Gtk.Box) -> None:
        section = Section("gh-keyboard-symbolic", "Hotkeys")
        self._fn_switch = ToggleRow(
            "Handle Fn / ROG keys",
            subtitle="Passive: keys keep their normal function.",
        )
        self._fn_switch.set_active(bool(self.window.settings.get("fn_enabled")))
        self._fn_switch.switch.connect("notify::active", self._on_fn_toggled)
        section.add(self._fn_switch)

        self._hotkey_status = ValueRow("Devices", "—")
        section.add(self._hotkey_status)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        configure = flat_button("Configure hotkeys\u2026", small=True)
        configure.connect("clicked", lambda *_: self.window.open_hotkeys_window())
        row.append(configure)
        section.add(row)
        body.append(section)

    def _build_window(self, body: Gtk.Box) -> None:
        section = Section("gh-screen-symbolic", "Window")
        self._hidden_switch = ToggleRow(
            "Start hidden in the tray",
            subtitle="Used by Run on Startup and by new launches.",
        )
        self._hidden_switch.set_active(bool(self.window.settings.get("start_hidden")))
        self._hidden_switch.switch.connect("notify::active", self._on_hidden_toggled)
        section.add(self._hidden_switch)
        body.append(section)

    def _build_about(self, body: Gtk.Box) -> None:
        section = Section("gh-settings-symbolic", "About")
        self._asusd_value = ValueRow("asusd", "—")
        self._cardwire_value = ValueRow("cardwire", "—")
        self._machine_value = ValueRow("Machine", machine_name())
        self._version_value = ValueRow("g-helper-linux", APP_VERSION)
        for row in (
            self._asusd_value,
            self._cardwire_value,
            self._machine_value,
            self._version_value,
        ):
            section.add(row)
        body.append(section)

    def _profile_dropdown(self) -> Gtk.DropDown:
        widget = dropdown([PROFILE_LABELS.get(p, str(p)) for p in self.window.profiles])
        return widget

    @staticmethod
    def _labeled(title: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        label = Gtk.Label(label=title, xalign=0.0)
        label.add_css_class("gh-live")
        box.append(label)
        box.append(widget)
        return box

    # -- data -------------------------------------------------------------
    def refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.window.run(self._collect, done=self._apply, error=self._failed)

    def _collect(self) -> dict:
        data: dict = {}
        data["profile_ac"] = self.asusd.profile_on_ac()
        data["profile_battery"] = self.asusd.profile_on_battery()
        data["auto"] = (
            self.asusd.change_profile_on_ac()
            or self.asusd.change_profile_on_battery()
        )
        data["linked_epp"] = self.asusd.linked_epp()
        from ..backend.system import read_text

        data["epp"] = read_text(
            "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"
        )
        data["attrs"] = {}
        for logical in (asusd_mod.ATTR_DGPU_DISABLE, asusd_mod.ATTR_GPU_MUX):
            try:
                data["attrs"][logical] = self.asusd.armoury(logical)
            except asusd_mod.AsusdError:
                continue
        data["pending"] = self.asusd.pending_reboot()
        data["asusd_version"] = self.asusd.version
        data["cardwire"] = cw.available()
        data["hotkeys"] = hotkeys_mod.probe()
        return data

    def _apply(self, data: dict) -> None:
        self._refresh_pending = False
        self._loading = True
        try:
            values = list(self.window.profiles)
            if data.get("profile_ac") in values:
                self._ac_combo.set_selected(values.index(data["profile_ac"]))
            if data.get("profile_battery") in values:
                self._battery_combo.set_selected(
                    values.index(data["profile_battery"])
                )
            self._auto_switch.set_active(bool(data.get("auto")))
            self._epp_switch.set_active(bool(data.get("linked_epp")))

            attrs = data.get("attrs") or {}
            dgpu = attrs.get(asusd_mod.ATTR_DGPU_DISABLE)
            if dgpu is not None:
                self._dgpu_switch.set_active(int(dgpu.get("CurrentValue", 0)) == 1)
                self._dgpu_switch.set_sensitive(True)
            else:
                self._dgpu_switch.set_sensitive(False)
            mux = attrs.get(asusd_mod.ATTR_GPU_MUX)
            if mux is not None:
                self._mux_combo.set_selected(
                    0 if int(mux.get("CurrentValue", 1)) == 0 else 1
                )
                self._mux_combo.set_sensitive(True)
            else:
                self._mux_combo.set_sensitive(False)
            self._pending_value.set_value(
                "reboot required" if data.get("pending") else "none"
            )
        finally:
            self._loading = False
        self._epp_value.set_value(data.get("epp") or "not exposed")
        self._asusd_value.set_value(str(data.get("asusd_version", "—")))
        self._cardwire_value.set_value("installed" if data.get("cardwire") else "missing")

        info = data.get("hotkeys") or {}
        devices = info.get("devices") or []
        if not devices:
            self._hotkey_status.set_value("no ASUS hotkey device found")
        elif info.get("readable"):
            self._hotkey_status.set_value("readable")
        else:
            self._hotkey_status.set_value("not readable \u2014 install the udev rule")

    # -- handlers ---------------------------------------------------------
    def _failed(self, exc: Exception) -> None:
        self._refresh_pending = False
        self.window.show_error(exc)
        self.refresh()

    def _on_auto_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._loading:
            return
        enabled = switch.get_active()

        def work() -> None:
            self.asusd.set_change_profile_on_ac(enabled)
            self.asusd.set_change_profile_on_battery(enabled)

        self.window.run(work, done=lambda _r: self.refresh(), error=self._failed)

    def _set_ac(self, index: int) -> None:
        values = list(self.window.profiles)
        if 0 <= index < len(values):
            profile = values[index]
            self.window.run(
                lambda: self.asusd.set_profile_on_ac(profile),
                done=lambda _r: self.refresh(),
                error=self._failed,
            )

    def _set_battery(self, index: int) -> None:
        values = list(self.window.profiles)
        if 0 <= index < len(values):
            profile = values[index]
            self.window.run(
                lambda: self.asusd.set_profile_on_battery(profile),
                done=lambda _r: self.refresh(),
                error=self._failed,
            )

    def _on_ac_selected(self, row: Gtk.DropDown, _param) -> None:
        if not self._loading:
            self._set_ac(row.get_selected())

    def _on_battery_selected(self, row: Gtk.DropDown, _param) -> None:
        if not self._loading:
            self._set_battery(row.get_selected())

    def _on_epp_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._loading:
            return
        enabled = switch.get_active()
        self.window.run(
            lambda: self.asusd.set_linked_epp(enabled),
            done=lambda _r: self.refresh(),
            error=self._failed,
        )

    def _on_fn_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._loading:
            return
        self.window.set_hotkeys_enabled(switch.get_active())
        self.refresh()

    def _on_hidden_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._loading:
            return
        hidden = switch.get_active()
        self.window.settings.set("start_hidden", hidden)
        if not autostart.enabled():
            return
        try:
            autostart.set_enabled(True, hidden=hidden)
        except OSError as exc:
            self._failed(exc)

    def _on_dgpu_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._loading:
            return
        value = 1 if switch.get_active() else 0

        def work() -> None:
            self.asusd.set_armoury(asusd_mod.ATTR_DGPU_DISABLE, value)
            try:
                self.asusd.apply_queued_gpu()
            except asusd_mod.AsusdError:
                pass

        self.window.run(work, done=lambda _r: self.refresh(), error=self._failed)

    def _on_mux_selected(self, row: Gtk.DropDown, _param) -> None:
        if self._loading:
            return
        value = 0 if row.get_selected() == 0 else 1

        def work() -> None:
            self.asusd.set_armoury(asusd_mod.ATTR_GPU_MUX, value)
            try:
                self.asusd.apply_queued_gpu()
            except asusd_mod.AsusdError:
                pass

        self.window.run(work, done=lambda _r: self.refresh(), error=self._failed)

    def _apply_queued(self) -> None:
        self.window.run(
            self.asusd.apply_queued_gpu,
            done=lambda _r: self.refresh(),
            error=self._failed,
        )

    def _on_destroy(self, *_args) -> None:
        self.window.forget_settings_window(self)
