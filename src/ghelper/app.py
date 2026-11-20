# SPDX-License-Identifier: GPL-3.0-or-later

"""Application object and process entry point."""

from __future__ import annotations

import logging
from pathlib import Path

from . import APP_ID, APP_NAME, APP_VERSION
from .arguments import parse_gui_arguments
from .deps import Adw, Gdk, Gio, GLib, Gtk
from .ui import theme
from .window import MainWindow

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.tray = None
        self.window: MainWindow | None = None
        self._state_applied = False
        self.connect("startup", self._on_startup)
        self.connect("shutdown", self._on_shutdown)

    # -- lifecycle -------------------------------------------------------
    def do_command_line(self, command_line) -> int:  # noqa: N802 - GObject virtual
        page, hidden = parse_gui_arguments(list(command_line.get_arguments()))
        self._show(page, hidden)
        return 0

    def do_activate(self) -> None:  # noqa: N802 - GObject virtual
        self._show("main", False)

    def _on_startup(self, _application: Adw.Application) -> None:
        Gtk.Window.set_default_icon_name(APP_ID)
        theme.install()
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.IconTheme.get_for_display(display).add_search_path(str(DATA_DIR))

        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

        about_action = Gio.SimpleAction(name="about")
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

        show_action = Gio.SimpleAction(name="show")
        show_action.connect("activate", lambda *_: self.show_window())
        self.add_action(show_action)

    def _on_shutdown(self, _application: Adw.Application) -> None:
        from .backend import kwin

        kwin.stop()
        if self.window is not None:
            self.window.stop_hotkeys()
            self.window.save_geometry()
            self.window.tasks.shutdown()

    # -- windows ---------------------------------------------------------
    def _ensure_window(self) -> MainWindow:
        if self.window is None:
            self.window = MainWindow(self)
        return self.window

    def _show(self, page: str, hidden: bool) -> None:
        window = self._ensure_window()
        # KWin evaluates a freshly loaded script asynchronously, so wait until
        # startup rather than installing it the moment an OSD is needed.
        window.install_watchers()
        self._ensure_tray(window)

        opener = {
            "fans": window.open_fans_window,
            "settings": window.open_settings_window,
            "hotkeys": window.open_hotkeys_window,
        }.get(page)
        if opener is not None:
            # A page request opens only that window.  Showing the main window
            # first would flash it, steal focus and contradict "--page X".
            GLib.idle_add(opener)
            return

        if hidden and self.tray is not None and self.tray.active:
            window.set_visible(False)
        else:
            self.show_window()

    def show_window(self) -> None:
        if self.window is None:
            return
        # Install the KWin watcher while the window is still unmapped: it then
        # positions the window before the first frame is drawn.
        self.window.install_placement()
        self.window.set_visible(True)
        self.window.present()
        if not self._state_applied:
            self._state_applied = True
            self.window.apply_saved_state()
        self.window.place_near_panel()

    def toggle_window(self) -> None:
        window = self.window
        if window is not None and window.is_visible():
            log.debug("hotkey: hiding window")
            window.set_visible(False)
        else:
            log.debug("hotkey: showing window")
            self.show_window()

    def _ensure_tray(self, window: MainWindow) -> None:
        if self.tray is not None:
            return
        try:
            from .tray import Tray

            self.tray = Tray(
                icon_path=DATA_DIR / f"{APP_ID}.png",
                profile=window.current_profile,
                gpu_state=window.gpu_quick_state(),
                on_show=self.show_window,
                on_quit=self.quit,
                on_profile=window.apply_profile,
                on_gpu=window.apply_gpu_mode,
            )
            window.attach_tray(self.tray)
        except Exception as exc:  # noqa: BLE001 - tray is optional
            log.info("tray icon unavailable: %s", exc)
            self.tray = None

    # -- dialogs ---------------------------------------------------------
    def _on_about(self, *_args) -> None:
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=APP_VERSION,
            comments="A lightweight G-Helper style control centre for ASUS laptops on Linux.\n"
            "Uses asusd for platform control and cardwire for GPU blocking.",
            license_type=Gtk.License.GPL_3_0,
        )
        dialog.present(self.get_active_window())
