# SPDX-License-Identifier: GPL-3.0-or-later

"""G-Helper-style main page: mode buttons, GPU, screen, keyboard, battery."""

from __future__ import annotations

import logging

from .. import APP_VERSION
from ..backend import asusd as asusd_mod
from ..backend import aura as aura_mod
from ..backend import autostart
from ..backend import cardwire as cw
from ..backend import display
from ..backend import gpu_policy
from ..backend.system import ac_online, dgpu_present
from ..deps import Adw, Gdk, GLib, Gtk
from .format import battery_line
from .widgets import (
    BatteryScale,
    ModeButton,
    Section,
    dropdown,
    flat_button,
    set_selected,
)

log = logging.getLogger(__name__)

PROFILE_META = {
    asusd_mod.QUIET: ("Silent", "gh-silent-symbolic", "green"),
    asusd_mod.LOW_POWER: ("Silent", "gh-silent-symbolic", "green"),
    asusd_mod.BALANCED: ("Balanced", "gh-balanced-symbolic", "blue"),
    asusd_mod.PERFORMANCE: ("Turbo", "gh-turbo-symbolic", "red"),
    asusd_mod.CUSTOM: ("Custom", "gh-optimized-symbolic", "orange"),
}

GPU_MODES = ("eco", "standard", "ultimate", "optimized")
GPU_LABELS = {
    "eco": "Eco",
    "standard": "Standard",
    "ultimate": "Ultimate",
    "optimized": "Optimized",
}
GPU_ICONS = {
    "eco": "gh-eco-symbolic",
    "standard": "gh-chip-symbolic",
    "ultimate": "gh-ultimate-symbolic",
    "optimized": "gh-optimized-symbolic",
}
GPU_ACCENTS = {
    "eco": "green",
    "standard": "blue",
    "ultimate": "blue",
    "optimized": "blue",
}


class MainView(Gtk.Box):
    def __init__(self, window) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.window = window
        self.asusd = window.asusd
        self.settings = window.settings
        self.aura = aura_mod.Aura()
        self._loading = False
        self._refresh_pending = False
        self._profile = window.current_profile
        self._gpu_auto = bool(self.settings.get("gpu_auto"))
        self._screen_auto = bool(self.settings.get("screen_auto"))
        self._attrs: dict[str, dict] = {}
        self._cw_current: str | None = None
        self._panel = None
        self._mode = None
        self._rates: list[float] = []
        self._last_ac: bool | None = None
        self._cw_auto: bool | None = None
        self._gpu_quick: str | None = None
        self._keyboard_built = False

        self.set_margin_top(10)
        self.set_margin_bottom(12)
        self.set_margin_start(10)
        self.set_margin_end(10)
        self.add_css_class("gh-root")

        self._build_performance()
        self._build_gpu()
        self._build_screen()
        self._build_keyboard()
        self._build_battery()
        self._build_footer()

        self.refresh()

    # -- construction ----------------------------------------------------
    def _build_performance(self) -> None:
        self.perf_section = Section("gh-fan-symbolic", "Performance Mode")
        grid = Gtk.Grid(column_spacing=6, column_homogeneous=True)
        self._profile_buttons: dict[int, ModeButton] = {}
        column = 0
        for profile in self.window.profiles:
            meta = PROFILE_META.get(profile)
            if meta is None:
                continue
            label, icon, accent = meta
            button = ModeButton(icon, label, accent)
            button.connect("clicked", self._on_profile_clicked, profile)
            grid.attach(button, column, 0, 1, 1)
            self._profile_buttons[profile] = button
            column += 1

        self._fans_button = ModeButton("gh-fan-symbolic", "Fans + Power", "blue")
        self._fans_button.add_css_class("secondary")
        self._fans_button.connect("clicked", lambda *_: self.window.open_fans_window())
        grid.attach(self._fans_button, column, 0, 1, 1)
        self.perf_section.add(grid)
        self.append(self.perf_section)

    def _build_gpu(self) -> None:
        self.gpu_section = Section("gh-chip-symbolic", "GPU Mode")
        grid = Gtk.Grid(column_spacing=6, column_homogeneous=True)
        self._gpu_buttons: dict[str, ModeButton] = {}
        for column, mode in enumerate(GPU_MODES):
            button = ModeButton(GPU_ICONS[mode], GPU_LABELS[mode], GPU_ACCENTS[mode])
            button.connect("clicked", self._on_gpu_clicked, mode)
            grid.attach(button, column, 0, 1, 1)
            self._gpu_buttons[mode] = button
        self.gpu_section.add(grid)
        self.append(self.gpu_section)

    def _build_screen(self) -> None:
        self.screen_section = Section("gh-screen-symbolic", "Laptop Screen")
        grid = Gtk.Grid(column_spacing=6, column_homogeneous=True)
        self._screen_buttons: dict[str, Gtk.Button] = {}
        for column, key in enumerate(("auto", "min", "max")):
            label = {"auto": "Auto", "min": "60Hz", "max": "120Hz + OD"}[key]
            button = flat_button(label, secondary=key != "auto")
            button.set_hexpand(True)
            button.set_size_request(-1, 32)
            button.connect("clicked", self._on_screen_clicked, key)
            grid.attach(button, column, 0, 1, 1)
            self._screen_buttons[key] = button
        self.screen_section.add(grid)
        self.append(self.screen_section)

    def _build_keyboard(self) -> None:
        self.keyboard_section = Section("gh-keyboard-symbolic", "Laptop Keyboard")
        self._keyboard_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._mode_dropdown = dropdown(["Static"])
        self._brightness_dropdown = dropdown(["Off", "Low", "Medium", "High"])
        self._mode_dropdown.connect("notify::selected", self._on_aura_mode)
        self._brightness_dropdown.connect("notify::selected", self._on_aura_brightness)
        self._color_button = None
        try:
            self._color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog())
        except AttributeError:  # older GTK
            pass
        self._color_dialog = Gtk.ColorDialog()
        self._color_text = flat_button("Color")
        self._color_text.connect("clicked", self._choose_color)
        self._keyboard_row.append(self._mode_dropdown)
        if self._color_button is not None:
            self._color_button.add_css_class("gh-color-button")
            self._color_button.connect("notify::rgba", self._on_aura_color)
            self._keyboard_row.append(self._color_button)
            self._keyboard_row.append(self._color_text)
        self._keyboard_row.append(self._brightness_dropdown)
        self.keyboard_section.add(self._keyboard_row)

        self._speed_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        speed_label = Gtk.Label(label="Speed", xalign=0.0)
        speed_label.add_css_class("gh-live")
        self._speed_row.append(speed_label)
        self._speed_dropdown = dropdown(
            [aura_mod.SPEED_LABELS[name] for name in aura_mod.SPEED_NAMES]
        )
        self._speed_dropdown.set_hexpand(False)
        self._speed_dropdown.connect("notify::selected", self._on_aura_speed)
        self._speed_row.append(self._speed_dropdown)
        self._speed_row.set_visible(False)
        self.keyboard_section.add(self._speed_row)

        self.keyboard_section.set_visible(False)
        self.append(self.keyboard_section)

    def _build_battery(self) -> None:
        self.battery_section = Section("gh-battery-symbolic", "Battery Charge Limit")
        self._full_button = flat_button("Full", secondary=True, small=True)
        self._full_button.set_valign(Gtk.Align.CENTER)
        self._full_button.connect("clicked", self._on_full_charge)
        self.battery_section.header.add_suffix(self._full_button)
        self.battery_scale = BatteryScale(self._on_battery_changed)
        self.battery_section.add(self.battery_scale)
        self.append(self.battery_section)

    def _build_footer(self) -> None:
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        version = Gtk.Label(label=f"Version {APP_VERSION}", xalign=0.0)
        version.add_css_class("gh-footer-label")
        footer.append(version)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._startup = Gtk.CheckButton(label="Run on Startup")
        self._startup.add_css_class("gh-check")
        try:
            self._startup.set_active(autostart.enabled())
        except OSError:
            self._startup.set_sensitive(False)
        self._startup.connect("toggled", self._on_startup_toggled)
        settings_button = flat_button("Settings")
        settings_button.connect("clicked", lambda *_: self.window.open_settings_window())
        quit_button = flat_button("Quit")
        quit_button.connect("clicked", lambda *_: self.window.quit_app())
        row.append(self._startup)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        row.append(settings_button)
        row.append(quit_button)
        footer.append(row)
        self.append(footer)

    # -- data ------------------------------------------------------------
    def refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        self.window.run(self._collect, done=self._apply, error=self._load_failed)

    def _load_failed(self, exc: Exception) -> None:
        self._refresh_pending = False
        self.window.show_error(exc, quiet=True)

    def _collect(self) -> dict:
        data: dict = {
            "profile": None,
            "cw_available": cw.available(),
            "cw_current": None,
            "cw_error": None,
            "attrs": {},
            "panel": None,
            "mode": None,
            "rates": [],
            "display_available": display.available(),
            "display_error": None,
            "aura": None,
            "limit": None,
        }
        try:
            data["profile"] = self.asusd.platform_profile()
        except Exception as exc:  # noqa: BLE001
            data["profile_error"] = str(exc)

        if data["cw_available"]:
            try:
                data["cw_current"] = cw.get_state().get("current")
            except cw.CardwireError as exc:
                data["cw_error"] = str(exc)
            # Asked on the worker: the CLI can block while the daemon retries.
            data["cw_auto"] = cw.battery_auto_switch()

        for logical in (
            asusd_mod.ATTR_DGPU_DISABLE,
            asusd_mod.ATTR_GPU_MUX,
            asusd_mod.ATTR_PANEL_OVERDRIVE,
        ):
            try:
                data["attrs"][logical] = self.asusd.armoury(logical)
            except asusd_mod.AsusdError:
                continue

        if data["display_available"]:
            try:
                panel = display.primary_panel()
                data["panel"] = panel
                if panel is not None:
                    mode = display.current_mode(panel)
                    data["mode"] = mode
                    data["rates"] = display.refresh_rates(panel, mode)
            except display.DisplayError as exc:
                data["display_error"] = str(exc)
        else:
            data["display_error"] = "kscreen-doctor not found (KDE only)"

        try:
            if self.aura.available:
                data["aura"] = self.aura.state()
        except aura_mod.AuraError:
            data["aura"] = None

        try:
            data["limit"] = self.asusd.charge_limit()
        except Exception:  # noqa: BLE001
            data["limit"] = None
        return data

    def _apply(self, data: dict) -> None:
        self._refresh_pending = False
        self._attrs = data.get("attrs") or {}
        self._cw_current = data.get("cw_current")
        self._cw_auto = data.get("cw_auto")
        self._panel = data.get("panel")
        self._mode = data.get("mode")
        self._rates = data.get("rates") or []
        if data.get("profile") is not None:
            self.set_profile(int(data["profile"]))
        self._apply_gpu(data)
        self._apply_screen(data)
        self._apply_keyboard(data)
        self._apply_battery(data)
        self._startup.set_active(autostart.enabled())

    def _apply_gpu(self, data: dict) -> None:
        mux = self._attrs.get(asusd_mod.ATTR_GPU_MUX)
        dgpu = self._attrs.get(asusd_mod.ATTR_DGPU_DISABLE)
        mux_value = int(mux.get("CurrentValue", 1)) if mux else None
        dgpu_value = int(dgpu.get("CurrentValue", 0)) if dgpu else None
        mux_queued = int(mux.get("QueuedGpuValue", -1)) if mux else -1
        dgpu_queued = int(dgpu.get("QueuedGpuValue", -1)) if dgpu else -1

        state = gpu_policy.selection(
            gpu_auto=self._gpu_auto,
            current=data.get("cw_current"),
            dgpu_value=dgpu_value,
            dgpu_queued=dgpu_queued,
            mux_value=mux_value,
            mux_queued=mux_queued,
        )
        self._gpu_quick = gpu_policy.tray_mode(state.selected, data.get("cw_current"))
        self.gpu_section.set_title(state.title)
        for mode, button in self._gpu_buttons.items():
            button.set_selected(mode == state.selected)
            button.set_sensitive(True)

    def _apply_screen(self, data: dict) -> None:
        panel = data.get("panel")
        mode = data.get("mode")
        rates = data.get("rates") or []
        overdrive = self._attrs.get(asusd_mod.ATTR_PANEL_OVERDRIVE)
        od_on = bool(int(overdrive.get("CurrentValue", 0))) if overdrive else False

        if panel is None or mode is None or not rates:
            self.screen_section.set_title("Laptop Screen")
            self.screen_section.set_live(data.get("display_error") or "")
            for button in self._screen_buttons.values():
                button.set_sensitive(False)
            self._screen_buttons["auto"].set_label("Auto")
            return

        max_rate = max(rates)
        min_rate = min(rates)
        self._screen_buttons["min"].set_label(f"{min_rate:.0f}Hz")
        self._screen_buttons["max"].set_label(f"{max_rate:.0f}Hz + OD")
        self._screen_buttons["auto"].set_label("Auto")

        if self._screen_auto:
            selected = "auto"
        elif abs(mode["rate"] - min_rate) < 0.3:
            selected = "min"
        else:
            selected = "max"

        suffix = " + Overdrive" if od_on else ""
        odd = "Auto · " if self._screen_auto else ""
        self.screen_section.set_title(
            f"Laptop Screen: {odd}{mode['rate']:.0f}Hz{suffix}"
        )
        self.screen_section.set_live("")
        for key, button in self._screen_buttons.items():
            set_selected(button, key == selected)
            button.set_sensitive(True)

    def _apply_keyboard(self, data: dict) -> None:
        state = data.get("aura")
        if not state:
            self.keyboard_section.set_visible(False)
            return
        self.keyboard_section.set_visible(True)
        self._loading = True
        try:
            modes = state.get("supported_modes") or [state.get("mode", 0)]
            self._mode_ids = [int(m) for m in modes]
            self._mode_dropdown.set_model(
                Gtk.StringList.new(
                    [aura_mod.MODE_NAMES.get(m, f"Mode {m}") for m in self._mode_ids]
                )
            )
            current_mode = int(state.get("mode", 0))
            if current_mode in self._mode_ids:
                self._mode_dropdown.set_selected(self._mode_ids.index(current_mode))

            # Speed only affects the animated modes; static ignores it.
            self._speed_row.set_visible(current_mode != 0)
            current_speed = str(state.get("speed") or "Med")
            if current_speed in aura_mod.SPEED_NAMES:
                self._speed_dropdown.set_selected(
                    aura_mod.SPEED_NAMES.index(current_speed)
                )

            brightness_values = state.get("supported_brightness") or [0, 1, 2, 3]
            self._brightness_values = [int(v) for v in brightness_values]
            self._brightness_dropdown.set_model(
                Gtk.StringList.new(
                    [aura_mod.BRIGHTNESS_NAMES.get(v, str(v)) for v in self._brightness_values]
                )
            )
            current_brightness = int(state.get("brightness", 0))
            if current_brightness in self._brightness_values:
                self._brightness_dropdown.set_selected(
                    self._brightness_values.index(current_brightness)
                )

            if self._color_button is not None:
                red, green, blue = state.get("color", (255, 255, 255))
                color = Gdk.RGBA()
                color.red = red / 255
                color.green = green / 255
                color.blue = blue / 255
                color.alpha = 1.0
                self._color_button.set_rgba(color)
        finally:
            self._loading = False

    def _apply_battery(self, data: dict) -> None:
        limit = data.get("limit")
        if limit is None:
            self.battery_section.set_title("Battery Charge Limit")
            self.battery_scale.set_sensitive(False)
            return
        self.battery_scale.set_sensitive(True)
        self.battery_section.set_title(f"Battery Charge Limit: {limit}%")
        self.battery_scale.set_value(int(limit))

    # -- live telemetry ---------------------------------------------------
    def update_live(self, snapshot: dict) -> None:
        cpu = snapshot.get("cpu_temp")
        fans = snapshot.get("fans") or {}
        cpu_fan = self._fan_text(fans.get("cpu_pwm"), fans.get("cpu"))
        gpu = (snapshot.get("gpu") or {}).get("dgpu")
        gpu_fan = self._fan_text(fans.get("gpu_pwm"), fans.get("gpu"))
        cpu_text = f"CPU: {cpu:.0f}\u00b0C" if cpu is not None else "CPU: \u2014"
        gpu_text = f"GPU: {gpu:.0f}\u00b0C" if gpu is not None else "GPU: \u2014"
        self.perf_section.set_live(f"{cpu_text} -  Fan: {cpu_fan}")
        self.gpu_section.set_live(f"{gpu_text} -  Fan: {gpu_fan}")
        self.battery_section.set_live(battery_line(snapshot.get("battery")))

    @staticmethod
    def _fan_text(pwm: int | None, rpm: int | None) -> str:
        if pwm is not None:
            return f"{round(pwm / 255 * 100)}%"
        if rpm is not None:
            return f"{round(min(rpm, 5600) / 5600 * 100)}%"
        return "\u2014"

    def note_power(self, ac: bool | None) -> None:
        if ac is None:
            return
        if self._last_ac is None:
            self._last_ac = ac
            self._apply_gpu_for_power(ac)
            if self._screen_auto:
                self._apply_auto_refresh(ac)
            return
        if ac == self._last_ac:
            return
        self._last_ac = ac
        self._apply_gpu_for_power(ac)
        if self._screen_auto:
            self._apply_auto_refresh(ac)

    def _firmware_gpu_disabled(self) -> bool:
        """True when the firmware ECO switch removed the dGPU from the bus."""
        attr = self._attrs.get(asusd_mod.ATTR_DGPU_DISABLE)
        if not attr:
            return False
        try:
            return int(attr.get("CurrentValue", 0)) == 1
        except (TypeError, ValueError):
            return False

    def _fresh_firmware_gpu_disabled(self) -> bool:
        """Ask asusd directly; used from the worker before touching cardwire."""
        attr = self._fresh_attr(asusd_mod.ATTR_DGPU_DISABLE)
        if not attr:
            return self._firmware_gpu_disabled()
        try:
            return int(attr.get("CurrentValue", 0)) == 1
        except (TypeError, ValueError):
            return self._firmware_gpu_disabled()

    def _fresh_attr(self, logical: str) -> dict | None:
        try:
            return self.asusd.armoury(logical)
        except asusd_mod.AsusdError:
            return self._attrs.get(logical)

    @staticmethod
    def _dgpu_present() -> bool | None:
        """Whether the dGPU is on the PCI bus; None when it cannot be read."""
        return dgpu_present()

    def _gpu_absent(self) -> bool:
        """True when the dGPU is not on the PCI bus (ECO until rescan/reboot)."""
        return self._dgpu_present() is False

    def _apply_gpu_for_power(self, ac: bool) -> None:
        if gpu_policy.automation_should_skip(
            gpu_auto=self._gpu_auto,
            cw_available=cw.available(),
            cw_auto=self._cw_auto,
            cw_current=self._cw_current,
            firmware_disabled=self._firmware_gpu_disabled(),
            dgpu_present=self._dgpu_present(),
        ):
            return
        target = gpu_policy.optimized_target(ac)
        self.window.run(
            lambda: cw.set_mode(target),
            done=lambda _r: self.refresh(),
            error=self.window.show_error,
        )

    # -- profile actions --------------------------------------------------
    def set_profile(self, profile: int) -> None:
        self._profile = profile
        key = profile
        if key not in self._profile_buttons and profile == asusd_mod.LOW_POWER:
            key = asusd_mod.QUIET
        for value, button in self._profile_buttons.items():
            button.set_selected(value == key)

    def _on_profile_clicked(self, _button: Gtk.Button, profile: int) -> None:
        self.window.apply_profile(profile)

    # -- GPU actions ------------------------------------------------------
    def set_gpu_auto(self, enabled: bool) -> None:
        """Record whether Optimized drives the GPU.

        Settings calls this when Smart mode takes over, so the main window
        stops claiming Optimized is in charge.
        """
        self._gpu_auto = bool(enabled)
        self.settings.set("gpu_auto", bool(enabled))

    def gpu_quick_state(self) -> str | None:
        """The tray's radio state, from the last refresh."""
        return self._gpu_quick

    def set_gpu_mode(self, mode: str) -> None:
        """Change modes from outside the window, e.g. the tray menu."""
        if mode in gpu_policy.TRAY_MODES:
            self._on_gpu_clicked(None, mode)

    def _on_gpu_clicked(self, _button: Gtk.Button, mode: str) -> None:
        if mode == "optimized":
            self.set_gpu_auto(True)

            def work() -> gpu_policy.OptimizedPlan:
                # Fresh reads on the worker: the cached attributes may still be
                # empty if the first refresh has not returned yet.
                plan = gpu_policy.optimized_plan(
                    firmware_disabled=self._fresh_firmware_gpu_disabled(),
                    dgpu_present=self._dgpu_present(),
                    ac=self._last_ac if self._last_ac is not None else ac_online(),
                )
                if plan.target is not None:
                    cw.set_mode(plan.target)
                return plan

            def done(plan: gpu_policy.OptimizedPlan) -> None:
                if plan.action == gpu_policy.OPTIMIZED_FIRMWARE_ECO:
                    self.window.toast(
                        "Optimized set; the dGPU stays off until ECO is undone"
                    )
                    self.refresh()
                elif plan.action == gpu_policy.OPTIMIZED_DGPU_ABSENT:
                    self.window.toast("Optimized set; the dGPU returns after a reboot")
                    self.refresh()
                else:
                    self._after_gpu("Optimized (auto)")

            self.window.run(work, done=done, error=self._gpu_failed)
            return

        self.set_gpu_auto(False)
        if mode == "eco":
            self._apply_gpu_mode("integrated", "Eco")
        elif mode == "standard":
            self._apply_gpu_mode("hybrid", "Standard")
        elif mode == "smart":
            self._apply_gpu_mode(gpu_policy.SMART_MODE, "Smart")
        elif mode == "ultimate":
            self._confirm_ultimate()

    def _apply_gpu_mode(self, cw_mode: str, label: str) -> None:
        def work() -> str:
            error: Exception | None = None
            firmware_disabled = self._fresh_firmware_gpu_disabled()
            present = self._dgpu_present()
            can_switch = gpu_policy.cardwire_can_switch(firmware_disabled, present)
            # An ECO-disabled GPU is not on the PCI bus; asking cardwire to
            # switch it only produces ENOENT.  The firmware writes below are
            # what actually change the state.
            if cw.available() and can_switch:
                try:
                    cw.set_mode(cw_mode)
                except cw.CardwireError as exc:
                    error = exc
            changed = False
            if gpu_policy.firmware_queue_needed(cw_mode):
                changed = self._queue_firmware_for(cw_mode)
            if error is not None and not changed:
                raise error
            return gpu_policy.mode_outcome(
                changed=changed,
                firmware_disabled=firmware_disabled,
                dgpu_present=present,
                cw_mode=cw_mode,
            )

        def done(result: str) -> None:
            if result == gpu_policy.MODE_REBOOT:
                self.window.toast("The dGPU returns after a reboot")
                self.refresh()
                return
            if result == gpu_policy.MODE_CHANGED:
                self._after_gpu(f"{label} (firmware after reboot)")
            else:
                self._after_gpu(label)
            if (
                result == gpu_policy.MODE_CHANGED
                and cw_mode == "hybrid"
                and not self._gpu_absent()
            ):
                # The dGPU returns asynchronously after ECO is undone.
                GLib.timeout_add(1500, self._retry_hybrid)

        self.window.run(work, done=done, error=self._gpu_failed)

    def _retry_hybrid(self) -> bool:
        if not cw.available():
            return False
        self.window.run(
            lambda: cw.set_mode("hybrid"),
            done=lambda _r: self.refresh(),
            error=self._gpu_failed,
        )
        return False

    def _queue_firmware_for(self, cw_mode: str) -> bool:
        dgpu = self._fresh_attr(asusd_mod.ATTR_DGPU_DISABLE)
        mux = self._fresh_attr(asusd_mod.ATTR_GPU_MUX)
        dgpu_value = int(dgpu.get("CurrentValue", 0)) if dgpu else None
        mux_value = int(mux.get("CurrentValue", 1)) if mux else None
        changed = False
        if cw_mode == "integrated":
            if dgpu_value == 0:
                self.asusd.set_armoury(asusd_mod.ATTR_DGPU_DISABLE, 1)
                changed = True
        elif cw_mode == "hybrid":
            if dgpu_value == 1:
                self.asusd.set_armoury(asusd_mod.ATTR_DGPU_DISABLE, 0)
                changed = True
            if mux_value == 0:
                self.asusd.set_armoury(asusd_mod.ATTR_GPU_MUX, 1)
                changed = True
        if changed:
            try:
                self.asusd.apply_queued_gpu()
            except asusd_mod.AsusdError:
                pass
        return changed

    def _ultimate_work(self) -> None:
        present = self._dgpu_present()
        firmware_disabled = self._fresh_firmware_gpu_disabled()
        can_switch = gpu_policy.cardwire_can_switch(firmware_disabled, present)
        error: Exception | None = None
        if cw.available() and can_switch:
            try:
                cw.set_mode("hybrid")
            except cw.CardwireError as exc:
                error = exc
        # Fresh reads: the cached attributes may not have arrived yet.
        if self._fresh_attr(asusd_mod.ATTR_DGPU_DISABLE):
            self.asusd.set_armoury(asusd_mod.ATTR_DGPU_DISABLE, 0)
        if self._fresh_attr(asusd_mod.ATTR_GPU_MUX):
            self.asusd.set_armoury(asusd_mod.ATTR_GPU_MUX, 0)
        try:
            self.asusd.apply_queued_gpu()
        except asusd_mod.AsusdError:
            pass
        if error is not None:
            raise error

    def _confirm_ultimate(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Switch to Ultimate GPU mode?",
            body="The discrete GPU will drive the internal display after a reboot. "
            "Make sure nothing important is running.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("switch", "Switch")
        dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)

        def on_response(_dialog, response: str) -> None:
            if response != "switch":
                return
            self.window.run(
                self._ultimate_work,
                done=lambda _r: self._after_gpu("Ultimate (reboot required)"),
                error=self._gpu_failed,
            )

        dialog.connect("response", on_response)
        dialog.present(self.window)

    def _after_gpu(self, label: str) -> None:
        self.window.toast(f"GPU mode: {label}")
        self.refresh()

    def _gpu_failed(self, exc: Exception) -> None:
        message = gpu_policy.cardwire_error_message(
            str(exc),
            firmware_disabled=self._firmware_gpu_disabled(),
            dgpu_present=self._dgpu_present(),
        )
        if message is not None:
            exc = RuntimeError(message)
        self.window.show_error(exc)
        self.refresh()

    # -- screen actions ---------------------------------------------------
    def _on_screen_clicked(self, _button: Gtk.Button, key: str) -> None:
        if key == "auto":
            self._screen_auto = True
            self.settings.set("screen_auto", True)
            ac = self._last_ac
            if ac is None:
                ac = True
            self._apply_auto_refresh(ac)
            return

        self._screen_auto = False
        self.settings.set("screen_auto", False)
        if not self._rates:
            return
        rate = min(self._rates) if key == "min" else max(self._rates)
        overdrive = 1 if key == "max" else 0
        self._set_screen(rate, overdrive)

    def _apply_auto_refresh(self, ac: bool) -> None:
        if not self._rates:
            return
        rate = max(self._rates) if ac else min(self._rates)
        self._set_screen(rate, None)

    def _set_screen(self, rate: float, overdrive: int | None) -> None:
        panel = self._panel
        mode = self._mode
        if panel is None or mode is None:
            return

        def work() -> None:
            display.set_refresh_rate(panel["name"], mode["width"], mode["height"], rate)
            if overdrive is not None and self._attrs.get(asusd_mod.ATTR_PANEL_OVERDRIVE):
                self.asusd.set_armoury(asusd_mod.ATTR_PANEL_OVERDRIVE, overdrive)

        self.window.run(
            work,
            done=lambda _r: self.refresh(),
            error=self.window.show_error,
        )

    # -- keyboard actions -------------------------------------------------
    def _on_aura_mode(self, row: Gtk.DropDown, _param) -> None:
        if self._loading:
            return
        index = row.get_selected()
        modes = getattr(self, "_mode_ids", [])
        if 0 <= index < len(modes):
            mode = modes[index]
            self.window.run(
                lambda: self.aura.set_mode(mode),
                done=lambda _r: self.refresh(),
                error=self.window.show_error,
            )

    def _on_aura_brightness(self, row: Gtk.DropDown, _param) -> None:
        if self._loading:
            return
        index = row.get_selected()
        values = getattr(self, "_brightness_values", [])
        if 0 <= index < len(values):
            value = values[index]
            self.window.run(
                lambda: self.aura.set_brightness(value),
                error=self.window.show_error,
            )

    def _on_aura_speed(self, row: Gtk.DropDown, _param) -> None:
        if self._loading:
            return
        index = row.get_selected()
        if 0 <= index < len(aura_mod.SPEED_NAMES):
            speed = aura_mod.SPEED_NAMES[index]
            self.window.run(
                lambda: self.aura.set_speed(speed),
                error=self.window.show_error,
            )

    def _on_aura_color(self, button: Gtk.ColorDialogButton, _param) -> None:
        if self._loading:
            return
        color = button.get_rgba()
        rgb = (
            round(color.red * 255),
            round(color.green * 255),
            round(color.blue * 255),
        )
        self.window.run(
            lambda: self.aura.set_color(rgb),
            error=self.window.show_error,
        )

    def _choose_color(self, _button: Gtk.Button) -> None:
        if self._color_button is None:
            return
        self._color_dialog.choose_rgba(
            self.window,
            self._color_button.get_rgba(),
            None,
            self._color_chosen,
        )

    def _color_chosen(self, dialog: Gtk.ColorDialog, result) -> None:
        try:
            rgba = dialog.choose_rgba_finish(result)
        except GLib.Error:
            return
        red, green, blue = (
            round(rgba.red * 255),
            round(rgba.green * 255),
            round(rgba.blue * 255),
        )
        if self._color_button is not None:
            self._loading = True
            self._color_button.set_rgba(rgba)
            self._loading = False
        self.window.run(
            lambda: self.aura.set_color((red, green, blue)),
            error=self.window.show_error,
        )

    # -- battery actions --------------------------------------------------
    def _on_battery_changed(self, value: int) -> None:
        self.battery_section.set_title(f"Battery Charge Limit: {value}%")
        self.window.run(
            lambda: self.asusd.set_charge_limit(value),
            done=lambda _r: self.window.toast(f"Charge limit: {value}%"),
            error=self.window.show_error,
        )

    def _on_full_charge(self, _button: Gtk.Button) -> None:
        self.window.run(
            self.asusd.one_shot_full_charge,
            done=lambda _r: self.window.toast("Charging to full once"),
            error=self.window.show_error,
        )

    # -- footer actions ---------------------------------------------------
    def _on_startup_toggled(self, button: Gtk.CheckButton) -> None:
        hidden = bool(self.settings.get("start_hidden"))
        try:
            autostart.set_enabled(button.get_active(), hidden=hidden)
        except OSError as exc:
            self.window.show_error(exc)
            button.set_active(autostart.enabled())

    # -- misc -------------------------------------------------------------
    def set_dark(self, dark: bool) -> None:
        return None
