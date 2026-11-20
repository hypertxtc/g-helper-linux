# SPDX-License-Identifier: GPL-3.0-or-later

"""Main window: G-Helper-style compact control panel."""

from __future__ import annotations

import logging
import time

from . import APP_ID
from .backend.asusd import (
    BALANCED,
    CUSTOM,
    LOW_POWER,
    PERFORMANCE,
    PROFILE_NAMES,
    PROFILE_ORDER,
    QUIET,
    Asusd,
    AsusdError,
)
from .backend import cardwire as cw
from .backend import power_memory, ppd
from .backend.settings import Settings
from .backend.system import Monitor, ac_online
from .deps import Adw, Gio, GLib, Gtk
from .tasks import TaskRunner
from .ui.fans_window import FansWindow
from .ui.format import status_line
from .ui.main_view import MainView
from .ui.profiles import PROFILE_LABELS
from .ui.settings_window import SettingsWindow
from .ui import theme

log = logging.getLogger(__name__)

MIN_WIDTH = 390
MIN_HEIGHT = 520
POWER_SETTLE_SECONDS = 2.5


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Gtk.Application) -> None:
        super().__init__(
            application=application,
            title="G-Helper",
            default_width=430,
            default_height=600,
            icon_name=APP_ID,
        )
        self.set_size_request(MIN_WIDTH, MIN_HEIGHT)
        self.tasks = TaskRunner()
        self.asusd = Asusd()
        self.monitor = Monitor()
        self.settings = Settings()
        self.set_default_size(
            max(MIN_WIDTH, int(self.settings.get("window_width") or 430)),
            max(MIN_HEIGHT, int(self.settings.get("window_height") or 600)),
        )
        log.debug(
            "window size %sx%s", self.get_default_size().width, self.get_default_size().height
        )
        self.asusd_available = False
        self._asusd_error = ""
        self._tray = None
        self._tick_id = 0
        self._profile = 0
        self._profile_seq = 0
        self._profile_writes_inflight = 0
        self._expected_profile: int | None = None
        self._power_settle_until = 0.0
        self._power_confirm_ids: list[int] = []
        self._profile_watch = None
        self._power_watch = None
        self._sync_pending = False
        self._tick_count = 0
        self._last_snapshot: dict = {}
        self._fans_window: FansWindow | None = None
        self._settings_window: SettingsWindow | None = None
        self._hotkeys_window = None
        self._hotkeys = None
        self._overlay = None
        self._last_ac: bool | None = ac_online()
        self._power_id = 0

        self.profiles = self._detect_profiles()
        try:
            self._profile = self.asusd.platform_profile()
        except Exception:  # noqa: BLE001
            self._profile = self.profiles[1] if len(self.profiles) > 1 else self.profiles[0]

        self.toasts = Adw.ToastOverlay()

        header = Adw.HeaderBar()
        header.add_css_class("gh-header")
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="G-Helper")
        title.add_css_class("gh-app-title")
        title_box.append(title)
        header.pack_start(title_box)
        header.set_title_widget(Gtk.Label(label=""))

        self.main_view = MainView(self)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.add_css_class("gh-scroll")
        self.scroll.set_child(self.main_view)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        self.banner = Adw.Banner(title="asusd is not available", button_label="Retry")
        self.banner.connect("button-clicked", self._on_retry_asusd)
        toolbar.add_top_bar(self.banner)
        toolbar.set_content(self.scroll)

        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)
        self._update_banner()
        theme.register_root(self)

        reload_action = Gio.SimpleAction(name="reload")
        reload_action.connect("activate", lambda *_: self.refresh_all())
        self.add_action(reload_action)
        application.set_accels_for_action("win.reload", ["<Control>r"])

        self.connect("close-request", self._on_close)
        self.connect("destroy", self._on_destroy)

        self._start_profile_watch()
        self._start_power_watch()
        self.start_hotkeys()
        self._tick()
        self._tick_id = GLib.timeout_add_seconds(2, self._tick)
        # A dedicated, cheap poll of the power-supply flag.  Telemetry only
        # runs every two seconds, and an OSD that appears three seconds after
        # the cable moves feels broken; this reads one sysfs file.
        self._power_id = GLib.timeout_add(500, self._poll_power)

    # -- setup ----------------------------------------------------------
    def _detect_profiles(self) -> list[int]:
        try:
            self.asusd.connect()
        except AsusdError as exc:
            self._asusd_error = str(exc)
            self.asusd_available = False
            log.warning("asusd unavailable: %s", exc)
            return list(PROFILE_ORDER)
        self.asusd_available = True
        self._asusd_error = ""
        try:
            choices = set(self.asusd.platform_choices())
        except AsusdError:
            choices = set(PROFILE_ORDER)
        quiet = QUIET if QUIET in choices else (LOW_POWER if LOW_POWER in choices else None)
        profiles: list[int] = []
        if quiet is not None:
            profiles.append(quiet)
        for profile in (BALANCED, PERFORMANCE, CUSTOM):
            if profile in choices:
                profiles.append(profile)
        return profiles or list(PROFILE_ORDER)

    def _update_banner(self) -> None:
        """Show the live asusd state; the text is rebuilt, not frozen."""
        if self.asusd_available:
            title = "asusd is available"
        elif self._asusd_error:
            title = f"asusd is not available. {self._asusd_error}"
        else:
            title = "asusd is not available"
        self.banner.set_title(title)
        self.banner.set_revealed(not self.asusd_available)

    def profile_label(self, profile: int) -> str:
        return PROFILE_LABELS.get(profile, PROFILE_NAMES.get(profile, str(profile)))

    @property
    def current_profile(self) -> int:
        return self._profile

    def attach_tray(self, tray) -> None:
        self._tray = tray
        tray.set_profile(self._profile)
        tray.set_gpu_state(self.main_view.gpu_quick_state())
        self._refresh_tray()

    # -- window state ----------------------------------------------------
    def apply_saved_state(self) -> None:
        if self.settings.get("window_maximized") and self.is_visible():
            self.maximize()

    def save_geometry(self) -> None:
        if self.is_maximized():
            self.settings.set("window_maximized", True)
            return
        width = self.get_width()
        height = self.get_height()
        if width < MIN_WIDTH or height < MIN_HEIGHT:
            return
        self.settings.set("window_width", width)
        self.settings.set("window_height", height)
        self.settings.set("window_maximized", False)

    def _refresh_tray(self) -> None:
        if self._tray is None or not self._tray.active:
            return
        snapshot = self._last_snapshot
        gpu = (snapshot.get("gpu") or {}).get("dgpu")
        line = status_line(
            self.profile_label(self._profile),
            snapshot.get("cpu_temp"),
            gpu,
            snapshot.get("ac_online"),
            snapshot.get("battery"),
            gpu_mode=self.main_view.gpu_quick_state(),
            pending_reboot=self.main_view.pending_reboot(),
        )
        self._tray.set_tooltip(line)
        self._tray.set_gpu_state(self.main_view.gpu_quick_state())

    def gpu_quick_state(self) -> str | None:
        return self.main_view.gpu_quick_state()

    def apply_gpu_mode(self, mode: str) -> None:
        self.main_view.set_gpu_mode(mode)

    def _placement_args(self) -> tuple[str, str | None]:
        application = self.get_application()
        resource_class = application.get_application_id() if application else None
        return self.get_title() or "G-Helper", resource_class

    def install_watchers(self) -> None:
        """Load the KWin scripts before any window needs placing.

        The overlay is shown from a timer, not from a click, so there is no
        moment later where a script can be loaded without racing the window's
        first map.  Both calls are no-ops once the scripts are running.
        """
        from .backend import kwin
        from .ui.overlay import CAPTION

        self.install_placement()
        kwin.install_overlay(CAPTION)

    def install_placement(self) -> None:
        """Ask KWin to position the window while it is first mapped."""
        if self.is_maximized():
            return
        from .backend import kwin

        caption, resource_class = self._placement_args()
        kwin.install(caption, resource_class)

    def place_near_panel(self) -> None:
        """Re-apply the placement after the window is on screen.

        The KWin watcher already positions the window at map time; this second
        pass covers a panel or screen-layout change that happened while the
        window was hidden.
        """
        if self.is_maximized():
            return
        GLib.timeout_add(80, self._place_now)

    def _place_now(self) -> bool:
        if not self.get_visible():
            return False
        from .backend import kwin

        caption, resource_class = self._placement_args()
        placed = kwin.place_window(caption, resource_class)
        log.debug("re-applied window placement: %s", placed)
        return False

    # -- hotkeys ----------------------------------------------------------
    def start_hotkeys(self) -> None:
        if self._hotkeys is not None or not self.settings.get("fn_enabled"):
            return
        from .backend.hotkeys import HotkeyError, HotkeyListener

        listener = HotkeyListener(self._on_hotkey)
        try:
            listener.start()
        except HotkeyError as exc:
            log.debug("%s", exc)
            return
        self._hotkeys = listener

    def stop_hotkeys(self) -> None:
        if self._hotkeys is not None:
            self._hotkeys.stop()
            self._hotkeys = None

    def set_hotkeys_enabled(self, enabled: bool) -> None:
        self.settings.set("fn_enabled", enabled)
        if enabled:
            self.start_hotkeys()
        else:
            self.stop_hotkeys()

    def _on_hotkey(self, event) -> None:
        actions = self.settings.get("fn_actions") or {}
        action = actions.get(str(event.code))
        if not action or action == "ignore":
            return
        log.debug("hotkey %s on %s -> %s", event.code, event.device, action)
        self._dispatch_hotkey(action)

    def _dispatch_hotkey(self, action: str) -> None:
        application = self.get_application()
        if action == "toggle_window":
            if application is not None:
                application.toggle_window()
        elif action == "show_window":
            if application is not None:
                application.show_window()
        elif action == "profile_next":
            if not self.profiles:
                return
            try:
                index = self.profiles.index(self._profile)
            except ValueError:
                index = 0
            self.apply_profile(self.profiles[(index + 1) % len(self.profiles)])
        elif action in ("profile_quiet", "profile_balanced", "profile_performance"):
            profile = self._profile_for(action)
            if profile is not None:
                self.apply_profile(profile)
        elif action.startswith("keyboard_"):
            self._hotkey_keyboard(action)

    def _profile_for(self, action: str) -> int | None:
        if action == "profile_quiet":
            for candidate in (QUIET, LOW_POWER):
                if candidate in self.profiles:
                    return candidate
            return None
        wanted = {
            "profile_balanced": BALANCED,
            "profile_performance": PERFORMANCE,
        }.get(action)
        return wanted if wanted in self.profiles else None

    def _hotkey_keyboard(self, action: str) -> None:
        def work() -> None:
            from .backend.aura import Aura

            aura = Aura()
            state = aura.state()
            values = sorted(
                int(value) for value in state.get("supported_brightness") or [0, 1, 2, 3]
            )
            if not values:
                return
            current = int(state.get("brightness", 0))
            if action == "keyboard_toggle":
                target = 0 if current > 0 else values[-1]
            elif action == "keyboard_up":
                target = next((value for value in values if value > current), current)
            else:
                target = next(
                    (value for value in reversed(values) if value < current), current
                )
            if target != current:
                aura.set_brightness(target)

        self.run(work, error=self.show_error)

    # -- user feedback ---------------------------------------------------
    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def show_error(self, exc: Exception, *, quiet: bool = False) -> None:
        log.warning("%s", exc)
        if quiet:
            return
        self.toasts.add_toast(Adw.Toast(title=str(exc), timeout=8))

    # -- background work -------------------------------------------------
    def run(self, work, *, done=None, error=None) -> None:
        self.tasks.submit(work, done=done, error=error)

    def action(self, work, *, then=None) -> None:
        done = (lambda _result: then()) if then is not None else None
        self.tasks.submit(work, done=done, error=self.show_error)

    # -- profile actions -------------------------------------------------
    def apply_profile(self, profile: int) -> None:
        if not self.asusd_available:
            self.toast("asusd is not running")
            return
        self._profile_seq += 1
        seq = self._profile_seq
        self._profile_writes_inflight += 1
        self._expected_profile = profile
        self._power_settle_until = 0.0
        self._cancel_power_confirms()
        self._apply_profile_ui(profile)
        label = self.profile_label(profile)

        def work() -> None:
            # Sync tuned-ppd before asusd so TuneD finishes changing profiles
            # before asusd applies fan curves and EPP.
            ppd.sync_profile(profile)
            self.asusd.set_platform_profile(profile)
            # G-Helper on Windows remembers the mode that was active on each
            # power source; mirror this explicit choice into asusd's own slot
            # so its power-event switching restores it later.
            power_memory.remember(self.asusd, profile)

        self.tasks.submit(
            work,
            done=lambda _result: self._profile_written(profile, label, seq),
            error=lambda exc: self._profile_write_failed(exc, seq),
        )

    def _profile_written(
        self, profile: int, label: str, seq: int | None = None
    ) -> None:
        self._profile_writes_inflight = max(0, self._profile_writes_inflight - 1)
        if seq is not None and seq != self._profile_seq:
            return
        self._expected_profile = None
        self._power_settle_until = 0.0
        self._apply_profile_ui(profile)
        self.toast(f"{label} mode")
        self._sync_profile(force=True)
        if self._settings_window is not None:
            self._settings_window.refresh()
        # A hotkey or tray switch is the only feedback when the window is
        # hidden, so mirror G-Helper's mode OSD there too.
        if not self.get_visible() and self._last_ac is not None:
            self.show_mode_overlay(profile, self._last_ac)

    def _profile_write_failed(
        self, exc: Exception, seq: int | None = None
    ) -> None:
        self._profile_writes_inflight = max(0, self._profile_writes_inflight - 1)
        if seq is None or seq == self._profile_seq:
            self._expected_profile = None
            self._power_settle_until = 0.0
        self.show_error(exc)
        self._sync_profile(force=True)

    def sync_profile_now(self) -> None:
        self._sync_profile(force=True)

    def _apply_profile_ui(self, profile: int) -> None:
        self._profile = profile
        self.main_view.set_profile(profile)
        if self._fans_window is not None:
            self._fans_window.refresh()
        if self._tray is not None:
            self._tray.set_profile(profile)
        self._refresh_tray()

    def refresh_all(self) -> None:
        self.monitor.refresh_devices()
        self._detect_profiles()
        self._update_banner()
        self.main_view.refresh()
        if self._fans_window is not None:
            self._fans_window.refresh()
        if self._settings_window is not None:
            self._settings_window.refresh()
        self._sync_profile(force=True)
        self._tick()

    def _on_retry_asusd(self, _banner: Adw.Banner) -> None:
        # _detect_profiles() reconnects and records the error either way.
        self.profiles = self._detect_profiles() or self.profiles
        self._update_banner()
        if self.asusd_available:
            self._start_profile_watch()
            self.refresh_all()
            self.toast(f"Connected to asusd {self.asusd.version}")
        else:
            self.toast("asusd is still not reachable")

    # -- windows ---------------------------------------------------------
    def open_fans_window(self) -> None:
        if self._fans_window is None:
            self._fans_window = FansWindow(self)
        self._fans_window.present()

    def forget_fans_window(self, _window) -> None:
        self._fans_window = None

    def open_settings_window(self) -> None:
        if self._settings_window is None:
            self._settings_window = SettingsWindow(self)
        self._settings_window.present()

    def forget_settings_window(self, _window) -> None:
        self._settings_window = None

    def open_hotkeys_window(self) -> None:
        if self._hotkeys_window is None:
            from .ui.hotkeys_window import HotkeysWindow

            self._hotkeys_window = HotkeysWindow(self)
        self._hotkeys_window.present()

    def forget_hotkeys_window(self, _window) -> None:
        self._hotkeys_window = None

    def quit_app(self) -> None:
        application = self.get_application()
        if application is not None:
            application.quit()

    # -- periodic refresh ------------------------------------------------
    @staticmethod
    def _remove_watch(watch) -> None:
        if watch is None:
            return
        try:
            watch.remove()
        except Exception:  # noqa: BLE001
            pass

    def _start_profile_watch(self) -> None:
        if self._profile_watch is not None:
            return
        try:
            self._profile_watch = self.asusd.watch_platform_profile(
                self._on_profile_signal
            )
        except Exception as exc:  # noqa: BLE001 - bus may be absent in tests
            log.debug("platform profile watch unavailable: %s", exc)

    def _stop_profile_watch(self) -> None:
        self._remove_watch(self._profile_watch)
        self._profile_watch = None

    def _start_power_watch(self) -> None:
        if self._power_watch is not None:
            return
        try:
            self._power_watch = ppd.watch_ac_online(self._on_upower_ac)
        except Exception as exc:  # noqa: BLE001 - bus may be absent in tests
            log.debug("UPower AC watch unavailable: %s", exc)

    def _stop_power_watch(self) -> None:
        self._remove_watch(self._power_watch)
        self._power_watch = None

    def _on_profile_signal(self, profile: int) -> None:
        if self._profile_writes_inflight > 0:
            if self._expected_profile is not None and profile == self._expected_profile:
                self._apply_profile_ui(profile)
            return
        if self._expected_profile is not None:
            if profile == self._expected_profile:
                self._expected_profile = None
                self._power_settle_until = 0.0
            elif time.monotonic() < self._power_settle_until:
                return
            else:
                self._expected_profile = None
                self._power_settle_until = 0.0
        self._profile_seq += 1
        self._apply_profile_ui(profile)

    def _sync_profile(self, *, force: bool = False) -> None:
        if not self.asusd_available or self._sync_pending:
            return
        self._sync_pending = True
        seq = self._profile_seq
        self.tasks.submit(
            self.asusd.platform_profile,
            done=lambda profile: self._sync_profile_done(profile, force, seq),
            error=self._sync_profile_failed,
        )

    def _sync_profile_done(
        self, profile: int, force: bool, seq: int | None = None
    ) -> None:
        self._sync_pending = False
        if self._profile_writes_inflight > 0:
            return
        if seq is not None and seq != self._profile_seq:
            return
        if self._expected_profile is not None:
            if profile == self._expected_profile:
                self._expected_profile = None
                self._power_settle_until = 0.0
            elif time.monotonic() < self._power_settle_until:
                return
            else:
                self._expected_profile = None
                self._power_settle_until = 0.0
        ui_drifted = getattr(self.main_view, "_profile", None) != profile
        if force or profile != self._profile or ui_drifted:
            self._apply_profile_ui(profile)

    def _sync_profile_failed(self, exc: Exception) -> None:
        self._sync_pending = False
        log.debug("profile sync failed: %s", exc)

    def _tick(self) -> bool:
        snapshot = None
        try:
            snapshot = self.monitor.snapshot()
        except Exception as exc:  # noqa: BLE001
            log.debug("telemetry failed: %s", exc)
        if snapshot is not None:
            self._last_snapshot = snapshot
            try:
                self.main_view.update_live(snapshot)
                self.main_view.note_power(snapshot.get("ac_online"))
            except Exception as exc:  # noqa: BLE001
                log.debug("telemetry UI update failed: %s", exc)
            self._refresh_tray()

        self._tick_count += 1
        self._sync_profile()
        if self._tick_count % 6 == 0:
            self.main_view.refresh()
        cw.reap()
        return True

    def _on_close(self, _window: Adw.ApplicationWindow) -> bool:
        self.save_geometry()
        if self._tray is not None and self._tray.active:
            self.set_visible(False)
            return True
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._cancel_power_confirms()
        self._stop_profile_watch()
        self._stop_power_watch()
        self.stop_hotkeys()
        self.tasks.shutdown()
        return False

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        # destroy() does not emit close-request, and a surviving timer would
        # keep calling into a dead window (a GTK critical in the UI tests).
        for attribute in ("_tick_id", "_power_id"):
            source = getattr(self, attribute, 0)
            if source:
                GLib.source_remove(source)
                setattr(self, attribute, 0)
        self._cancel_power_confirms()
        self._stop_profile_watch()
        self._stop_power_watch()
        if self._overlay is not None:
            self._overlay.dismiss()

    # -- power-source changes --------------------------------------------
    def _poll_power(self) -> bool:
        """Watch the cheap AC flag so the OSD follows the cable, not the tick."""
        ac = ac_online()
        if ac is not None:
            self._handle_power_change(ac)
        return True

    def _on_upower_ac(self, ac: bool) -> None:
        self._handle_power_change(ac)

    def _handle_power_change(self, ac: bool) -> None:
        if self._last_ac is None:
            self._last_ac = ac
            return
        if ac == self._last_ac:
            return
        self._last_ac = ac
        self._profile_seq += 1
        seq = self._profile_seq
        log.debug("power source changed to %s", "AC" if ac else "battery")
        self._announce_power(ac, seq)

    def _announce_power(self, ac: bool, seq: int | None = None) -> None:
        """Coordinate tuned-ppd and asusd on power-source transitions."""
        self._profile_writes_inflight += 1
        self._expected_profile = None
        self._power_settle_until = 0.0
        self._cancel_power_confirms()

        def probe() -> tuple[int, bool]:
            remembered = power_memory.target(self.asusd, ac)
            if remembered is None:
                return self.asusd.platform_profile(), False
            GLib.idle_add(self._begin_power_ui, remembered, ac, seq)
            if seq is not None and self._profile_seq != seq:
                return remembered, True
            ppd.sync_profile(remembered)
            if seq is not None and self._profile_seq != seq:
                return remembered, True
            try:
                self.asusd.set_platform_profile(remembered)
            except AsusdError as exc:
                log.debug("power switch profile assert failed: %s", exc)
            return remembered, True

        self.tasks.submit(
            probe,
            done=lambda res: self._finish_power_switch(
                res[0], ac, switched=res[1], seq=seq
            ),
            error=lambda exc: self._power_switch_failed(exc, ac, seq),
        )

    def _begin_power_ui(
        self, profile: int, ac: bool, seq: int | None = None
    ) -> bool:
        if seq is not None and seq != self._profile_seq:
            return False
        self._expected_profile = profile
        self._power_settle_until = time.monotonic() + POWER_SETTLE_SECONDS
        self._apply_profile_ui(profile)
        self.show_mode_overlay(profile, ac)
        return False

    def _finish_power_switch(
        self,
        profile: int,
        ac: bool,
        *,
        switched: bool = False,
        seq: int | None = None,
    ) -> None:
        self._profile_writes_inflight = max(0, self._profile_writes_inflight - 1)
        if seq is not None and seq != self._profile_seq:
            return
        if not switched:
            self._apply_profile_ui(profile)
            self.show_mode_overlay(profile, ac)
        else:
            self._expected_profile = profile
            self._power_settle_until = max(
                self._power_settle_until, time.monotonic() + POWER_SETTLE_SECONDS
            )
            self._apply_profile_ui(profile)
        self._schedule_power_confirms()

    def _power_switch_failed(
        self, exc: Exception, ac: bool, seq: int | None = None
    ) -> None:
        self._profile_writes_inflight = max(0, self._profile_writes_inflight - 1)
        log.debug("power switch probe failed: %s", exc)
        if seq is not None and seq != self._profile_seq:
            return
        self._expected_profile = None
        self._power_settle_until = 0.0
        self.show_mode_overlay(self._profile, ac)
        self._schedule_power_confirms()

    def _cancel_power_confirms(self) -> None:
        for source_id in self._power_confirm_ids:
            GLib.source_remove(source_id)
        self._power_confirm_ids.clear()

    def _schedule_power_confirms(self) -> None:
        self._cancel_power_confirms()
        delays = (800, int(POWER_SETTLE_SECONDS * 1000) + 100)
        for delay_ms in delays:
            holder: list[int] = []

            def _confirm(_holder: list[int] = holder) -> bool:
                if _holder and _holder[0] in self._power_confirm_ids:
                    self._power_confirm_ids.remove(_holder[0])
                self._sync_profile(force=True)
                return False

            source_id = GLib.timeout_add(delay_ms, _confirm)
            holder.append(source_id)
            self._power_confirm_ids.append(source_id)

    def show_mode_overlay(self, profile: int, ac: bool) -> None:
        """Display the mode OSD; harmless on compositors without placement."""
        from .backend import kwin
        from .ui.overlay import CAPTION, PowerOverlay

        kwin.install_overlay(CAPTION)
        if self._overlay is None:
            self._overlay = PowerOverlay(self.get_application())
            self._overlay.connect("destroy", self._overlay_destroyed)
        self._overlay.show_status(profile, ac)

    def _overlay_destroyed(self, _widget: Gtk.Widget) -> None:
        self._overlay = None

