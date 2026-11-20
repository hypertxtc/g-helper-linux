# SPDX-License-Identifier: GPL-3.0-or-later

"""Settings window for controls that G-Helper keeps off the main page."""

from __future__ import annotations

import os
from pathlib import Path

from .. import APP_VERSION
from ..backend import asusd as asusd_mod
from ..backend import autostart
from ..backend import cardwire as cw
from ..backend import gpu_policy
from ..backend import hotkeys as hotkeys_mod
from ..backend.system import dgpu_present, machine_name, rescan_pci
from ..deps import Adw, Gio, GLib, Gtk, Pango
from .format import battery_line
from .profiles import PROFILE_LABELS
from .theme import register_root
from .widgets import Section, ToggleRow, ValueRow, dropdown, flat_button


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
        self.settings = window.settings
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
        self._build_gpu_apps(body)
        self._build_battery(body)
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
            "Remember a profile per power source",
            subtitle=(
                "Each mode you pick is stored for the current source, and asusd "
                "switches to it when the cable moves — even while this app is closed."
            ),
        )
        self._auto_switch.switch.connect("notify::active", self._on_auto_toggled)
        section.add(self._auto_switch)

        self._ac_combo = self._profile_dropdown()
        self._battery_combo = self._profile_dropdown()
        self._ac_combo.connect("notify::selected", self._on_ac_selected)
        self._battery_combo.connect("notify::selected", self._on_battery_selected)
        section.add(self._labeled("Remembered on AC power", self._ac_combo))
        section.add(self._labeled("Remembered on battery", self._battery_combo))
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
            subtitle="Removes the dGPU from the bus until the next boot.",
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
        rescan = flat_button("Rescan dGPU", small=True)
        rescan.set_tooltip_text(
            "Re-enumerate the PCI bus after the firmware ECO switch is turned "
            "off, so the dGPU comes back without a reboot."
        )
        rescan.connect("clicked", lambda *_: self.rescan_dgpu())
        self._rescan_button = rescan
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.append(apply)
        row.append(rescan)
        section.add(row)
        body.append(section)

    def _build_gpu_apps(self, body: Gtk.Box) -> None:
        section = Section("gh-chip-symbolic", "dGPU Apps")
        self._smart_switch = ToggleRow(
            "Keep the dGPU blocked (Smart)",
            subtitle="Only apps launched here may use the discrete GPU.",
        )
        self._smart_switch.switch.connect("notify::active", self._on_smart_toggled)
        section.add(self._smart_switch)

        hint = Gtk.Label(
            label="Games and renderers that need the discrete GPU can be "
            "started from here without leaving Smart mode.",
            xalign=0.0,
            wrap=True,
        )
        hint.add_css_class("gh-live")
        section.add(hint)

        self._gpu_apps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        section.add(self._gpu_apps_box)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        add = flat_button("Add app\u2026", small=True)
        add.connect("clicked", lambda *_: self.add_gpu_app())
        if not cw.available():
            add.set_sensitive(False)
            missing = Gtk.Label(label="cardwire is not installed", xalign=0.0)
            missing.add_css_class("gh-status-error")
            row.append(missing)
        row.append(add)
        section.add(row)
        body.append(section)
        self._rebuild_gpu_apps()

    def _rebuild_gpu_apps(self) -> None:
        child = self._gpu_apps_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._gpu_apps_box.remove(child)
            child = following

        apps = dict(self.settings.get("gpu_apps") or {})
        if not apps:
            empty = Gtk.Label(label="No apps added yet.", xalign=0.0)
            empty.add_css_class("gh-live")
            self._gpu_apps_box.append(empty)
            return
        for path, name in apps.items():
            self._gpu_apps_box.append(self._gpu_app_row(path, name))

    def _gpu_app_row(self, path: str, name: str) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label=name, xalign=0.0)
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_tooltip_text(path)
        launch = flat_button("Launch", small=True)
        launch.connect("clicked", lambda *_: self.launch_gpu_app(path, name))
        remove = Gtk.Button(label="\u00d7")
        remove.add_css_class("gh-flat-icon")
        remove.set_tooltip_text(f"Remove {name}")
        remove.connect("clicked", lambda *_: self.remove_gpu_app(path))
        row.append(label)
        row.append(launch)
        row.append(remove)
        return row

    def add_gpu_app(self) -> None:
        dialog = Gtk.FileDialog(title="Add an application")
        # Executables live in /usr/bin; starting there beats the cwd.
        candidates = (Path("/usr/bin"), Path("/usr/local/bin"))
        for folder in candidates:
            if folder.is_dir():
                dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
                break
        dialog.open(self, None, self._on_gpu_app_chosen)

    def _on_gpu_app_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        path = file.get_path() if file is not None else None
        if path:
            self._add_gpu_app_path(path)

    def _add_gpu_app_path(self, path: str) -> bool:
        if not os.path.isfile(path) or not os.access(path, os.X_OK):
            self.window.show_error(
                ValueError(f"{Path(path).name} is not an executable file")
            )
            return False
        apps = dict(self.settings.get("gpu_apps") or {})
        apps[path] = Path(path).name
        self.settings.set("gpu_apps", apps)
        self._rebuild_gpu_apps()
        self.window.toast(f"Added {Path(path).name}")
        return True

    def launch_gpu_app(self, path: str, name: str) -> None:
        def work() -> None:
            if not cw.daemon_alive():
                raise cw.CardwireError("cardwire daemon is not running; start cardwired")
            cw.spawn([path])

        self.window.run(
            work,
            done=lambda _r: self.window.toast(f"Launching {name} with the dGPU"),
            error=self.window.show_error,
        )

    def remove_gpu_app(self, path: str) -> None:
        apps = dict(self.settings.get("gpu_apps") or {})
        if apps.pop(path, None) is None:
            return
        self.settings.set("gpu_apps", apps)
        self._rebuild_gpu_apps()

    def _on_smart_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._loading:
            return
        enabled = switch.get_active()
        target = gpu_policy.SMART_MODE if enabled else "hybrid"

        def done(_result) -> None:
            if enabled:
                # Smart is an explicit choice; Optimized must not override it
                # on the next AC/battery change.
                self.window.main_view.set_gpu_auto(False)
            self.window.toast(f"GPU mode: {'Smart' if enabled else 'Standard'}")
            self.window.main_view.refresh()
            self.refresh()

        self.window.run(lambda: cw.set_mode(target), done=done, error=self._failed)

    def _build_battery(self, body: Gtk.Box) -> None:
        section = Section("gh-battery-symbolic", "Battery")
        self._charge_value = ValueRow("Charge", "\u2014")
        self._health_value = ValueRow("Health", "\u2014")
        self._cycles_value = ValueRow("Cycles", "\u2014")
        self._limit_value = ValueRow("Charge limit", "\u2014")
        for row in (
            self._charge_value,
            self._health_value,
            self._cycles_value,
            self._limit_value,
        ):
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
        section = Section("gh-power-symbolic", "Startup")
        self._boot_sound_switch = ToggleRow(
            "Boot sound",
            subtitle="Play the POST beep when the laptop powers on.",
        )
        self._boot_sound_switch.switch.connect(
            "notify::active", self._on_boot_sound_toggled
        )
        section.add(self._boot_sound_switch)

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
        self.window.run(self._collect, done=self._apply, error=self._load_failed)

    def _load_failed(self, exc: Exception) -> None:
        # Never retry from here: asusd being down would turn this into an
        # endless refresh loop that floods the journal.  A later manual or
        # automatic refresh picks the state up again.
        self._refresh_pending = False
        self.window.show_error(exc, quiet=True)

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
        for logical in (
            asusd_mod.ATTR_DGPU_DISABLE,
            asusd_mod.ATTR_GPU_MUX,
            asusd_mod.ATTR_BOOT_SOUND,
        ):
            try:
                data["attrs"][logical] = self.asusd.armoury(logical)
            except asusd_mod.AsusdError:
                continue
        data["pending"] = self.asusd.pending_reboot()
        data["asusd_version"] = self.asusd.version
        data["cardwire"] = cw.available()
        data["cw_current"] = None
        if data["cardwire"]:
            try:
                data["cw_current"] = cw.get_state().get("current")
            except cw.CardwireError:
                pass
        data["hotkeys"] = hotkeys_mod.probe()
        data["battery"] = self.window.monitor.battery()
        data["dgpu_present"] = dgpu_present()
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
            # Only useful when the GPU is missing and firmware is no longer
            # holding it off.
            cleared = dgpu is None or int(dgpu.get("CurrentValue", 0)) == 0
            self._rescan_button.set_sensitive(
                data.get("dgpu_present") is False and cleared
            )
            current = (data.get("cw_current") or "").lower()
            self._smart_switch.set_active(current == gpu_policy.SMART_MODE)
            self._smart_switch.set_sensitive(
                bool(data.get("cardwire"))
                and bool(current)
                and data.get("dgpu_present") is not False
                and cleared
            )
            boot = attrs.get(asusd_mod.ATTR_BOOT_SOUND)
            if boot is not None:
                self._boot_sound_switch.set_active(
                    int(boot.get("CurrentValue", 0)) == 1
                )
                self._boot_sound_switch.set_sensitive(True)
            else:
                self._boot_sound_switch.set_sensitive(False)
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

        self._apply_battery_values(data.get("battery") or {})

    def _apply_battery_values(self, battery: dict) -> None:
        if not battery:
            for row in (
                self._charge_value,
                self._health_value,
                self._cycles_value,
                self._limit_value,
            ):
                row.set_value("not detected")
            return
        self._charge_value.set_value(battery_line(battery) or "unknown")

        health = battery.get("health")
        full = battery.get("full_wh")
        design = battery.get("design_wh")
        if health is None:
            self._health_value.set_value("not reported")
        elif full and design:
            self._health_value.set_value(
                f"{float(health):.0f}% ({float(full):.1f} of {float(design):.1f} Wh)"
            )
        else:
            self._health_value.set_value(f"{float(health):.0f}%")

        cycles = battery.get("cycles")
        self._cycles_value.set_value(str(int(cycles)) if cycles else "not reported")

        limit = battery.get("limit")
        self._limit_value.set_value(
            f"{int(limit)}%" if limit is not None else "not reported"
        )

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

    def _on_boot_sound_toggled(self, switch: Gtk.Switch, _param) -> None:
        if self._loading:
            return
        enabled = switch.get_active()

        def done(_result) -> None:
            state = "enabled" if enabled else "disabled"
            self.window.toast(f"Boot sound {state} for the next power-on")
            self.refresh()

        self.window.run(
            lambda: self.asusd.set_armoury(
                asusd_mod.ATTR_BOOT_SOUND, 1 if enabled else 0
            ),
            done=done,
            error=self._failed,
        )

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

        def done(_result) -> None:
            self.refresh()
            if value == 0:
                self.window.toast("ECO off: use Rescan dGPU to bring it back now")

        self.window.run(work, done=done, error=self._failed)

    def rescan_dgpu(self) -> None:
        self._rescan_button.set_sensitive(False)

        def work() -> bool:
            rescan_pci()
            if cw.available():
                cw.refresh_gpus()
            return dgpu_present() is True

        def done(found: bool) -> None:
            if found:
                self.window.toast("dGPU is back")
            else:
                self.window.toast("No dGPU found; a reboot may be needed")
            self.refresh()

        self.window.run(work, done=done, error=self._rescan_failed)

    def _rescan_failed(self, exc: Exception) -> None:
        self.window.show_error(exc)
        self.refresh()

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
