# SPDX-License-Identifier: GPL-3.0-or-later

"""Main window: G-Helper-style compact control panel."""

from __future__ import annotations

import logging

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
from .backend.settings import Settings
from .backend.system import Monitor
from .deps import Adw, Gio, GLib, Gtk
from .tasks import TaskRunner
from .ui.fans_window import FansWindow
from .ui.format import status_line
from .ui.main_view import MainView
from .ui.settings_window import SettingsWindow
from .ui import theme

log = logging.getLogger(__name__)

PROFILE_LABELS = {
    QUIET: "Silent",
    LOW_POWER: "Silent",
    BALANCED: "Balanced",
    PERFORMANCE: "Turbo",
    CUSTOM: "Custom",
}

MIN_WIDTH = 390
MIN_HEIGHT = 520


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
        self._sync_pending = False
        self._tick_count = 0
        self._last_snapshot: dict = {}
        self._fans_window: FansWindow | None = None
        self._settings_window: SettingsWindow | None = None
        self._hotkeys_window = None
        self._hotkeys = None

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

        self.banner = Adw.Banner(
            title=f"asusd is not available. {self._asusd_error}".strip(),
            button_label="Retry",
        )
        self.banner.connect("button-clicked", self._on_retry_asusd)
        self.banner.set_revealed(not self.asusd_available)
        toolbar.add_top_bar(self.banner)
        toolbar.set_content(self.scroll)

        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)
        theme.register_root(self)

        reload_action = Gio.SimpleAction(name="reload")
        reload_action.connect("activate", lambda *_: self.refresh_all())
        self.add_action(reload_action)
        application.set_accels_for_action("win.reload", ["<Control>r"])

        self.connect("close-request", self._on_close)

        self.start_hotkeys()
        self._tick()
        self._tick_id = GLib.timeout_add_seconds(2, self._tick)

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

    def profile_label(self, profile: int) -> str:
        return PROFILE_LABELS.get(profile, PROFILE_NAMES.get(profile, str(profile)))

    @property
    def current_profile(self) -> int:
        return self._profile

    def attach_tray(self, tray) -> None:
        self._tray = tray
        tray.set_profile(self._profile)
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
        )
        self._tray.set_tooltip(line)

    def _placement_args(self) -> tuple[str, str | None]:
        application = self.get_application()
        resource_class = application.get_application_id() if application else None
        return self.get_title() or "G-Helper", resource_class

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
        label = self.profile_label(profile)
        self.tasks.submit(
            lambda: self.asusd.set_platform_profile(profile),
            done=lambda _result: self._profile_written(profile, label),
            error=self._profile_write_failed,
        )

    def _profile_written(self, profile: int, label: str) -> None:
        self._apply_profile_ui(profile)
        self.toast(f"{label} mode")
        self._sync_profile(force=True)

    def _profile_write_failed(self, exc: Exception) -> None:
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
        self.banner.set_revealed(not self.asusd_available)
        self.main_view.refresh()
        if self._fans_window is not None:
            self._fans_window.refresh()
        if self._settings_window is not None:
            self._settings_window.refresh()
        self._sync_profile(force=True)
        self._tick()

    def _on_retry_asusd(self, _banner: Adw.Banner) -> None:
        try:
            self.asusd.connect()
        except AsusdError:
            pass
        self.asusd_available = self.asusd.available
        self.profiles = self._detect_profiles() or self.profiles
        self.banner.set_revealed(not self.asusd_available)
        if self.asusd_available:
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
    def _sync_profile(self, *, force: bool = False) -> None:
        if not self.asusd_available or self._sync_pending:
            return
        self._sync_pending = True
        self.tasks.submit(
            self.asusd.platform_profile,
            done=lambda profile: self._sync_profile_done(profile, force),
            error=self._sync_profile_failed,
        )

    def _sync_profile_done(self, profile: int, force: bool) -> None:
        self._sync_pending = False
        if force or profile != self._profile:
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
        return True

    def _on_close(self, _window: Adw.ApplicationWindow) -> bool:
        self.save_geometry()
        if self._tray is not None and self._tray.active:
            self.set_visible(False)
            return True
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self.stop_hotkeys()
        self.tasks.shutdown()
        return False
