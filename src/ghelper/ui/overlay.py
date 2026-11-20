# SPDX-License-Identifier: GPL-3.0-or-later

"""Small on-screen status card for power-source changes.

asusd switches the performance profile on its own when the cable is plugged or
pulled, and the window is usually hidden in the tray at that moment, so the
change would otherwise be invisible.  This mirrors G-Helper's OSD: a borderless
card near the bottom of the screen naming the mode that is now active, shown
for about two seconds and then gone.

The window is created once and reused.  It takes no input, is excluded from the
task bar by the KWin rule in ``backend/kwin.py`` where that is available, and
falls back to whatever placement the compositor chooses elsewhere.  The card is
always dark, like a system OSD, so the text stays readable over any wallpaper.
"""

from __future__ import annotations

from ..deps import Adw, GLib, Gtk
from .profiles import icon_name, label

CAPTION = "G-Helper status"

FADE_IN_MS = 120
HOLD_MS = 1600
FADE_OUT_MS = 220


class PowerOverlay(Gtk.Window):
    def __init__(self, application: Gtk.Application | None = None) -> None:
        # Registering with the application matters: an unassociated toplevel
        # makes GtkApplication consider itself windowless while it is open.
        super().__init__(
            application=application, title=CAPTION, decorated=False, resizable=False
        )
        self.set_hide_on_close(True)
        self.set_can_focus(False)
        self.add_css_class("gh-overlay")

        self._card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self._card.add_css_class("gh-overlay-card")
        self._card.set_halign(Gtk.Align.CENTER)
        self._card.set_valign(Gtk.Align.CENTER)

        self._icon = Gtk.Image()
        self._icon.set_pixel_size(40)
        self._card.append(self._icon)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_valign(Gtk.Align.CENTER)
        self._title = Gtk.Label(xalign=0)
        self._title.add_css_class("gh-overlay-title")
        self._source = Gtk.Label(xalign=0)
        self._source.add_css_class("gh-overlay-sub")
        text.append(self._title)
        text.append(self._source)
        self._card.append(text)

        self.set_child(self._card)

        # GObject-owned animations must stay referenced or they are collected
        # mid-flight; the hard-stop timer guarantees the card cannot get stuck
        # on screen if the frame clock never advances.
        self._animation: Adw.TimedAnimation | None = None
        self._hide_id = 0
        self._stop_id = 0
        self.connect("destroy", self._on_destroy)

    # -- public API -------------------------------------------------------
    def show_status(self, profile: int, ac: bool) -> None:
        """Show the card for ``profile`` and the power source that is now active."""
        self._cancel()
        self._icon.set_from_icon_name(icon_name(profile, dark=True))
        self._title.set_text(f"{label(profile)} mode")
        self._source.set_text("Plugged in" if ac else "On battery")
        self._card.set_opacity(0.0)
        # set_visible(), not present(): present() asks the compositor to
        # activate the window, which would take keyboard focus mid-typing.
        self.set_visible(True)
        self._fade(1.0, FADE_IN_MS, self._schedule_hide)

    def dismiss(self) -> None:
        """Hide immediately, cancelling any pending animation or timer."""
        self._cancel()
        self._card.set_opacity(0.0)
        self.set_visible(False)

    # -- animation --------------------------------------------------------
    def _fade(self, target: float, duration: int, then=None) -> None:
        animation = Adw.TimedAnimation(
            widget=self._card,
            value_from=self._card.get_opacity(),
            value_to=target,
            duration=duration,
            easing=Adw.Easing.EASE_OUT_CUBIC,
            target=Adw.PropertyAnimationTarget.new(self._card, "opacity"),
        )
        if then is not None:
            animation.connect("done", lambda *_args: then())
        self._animation = animation
        animation.play()

    def _schedule_hide(self) -> None:
        if self._hide_id:
            GLib.source_remove(self._hide_id)
        self._hide_id = GLib.timeout_add(HOLD_MS, self._fade_out)

    def _fade_out(self) -> bool:
        self._hide_id = 0
        self._fade(0.0, FADE_OUT_MS, self._finish)
        # Belt and braces: an unmapped or stalled frame clock must not leave a
        # dark rectangle sitting over the user's desktop.
        self._stop_id = GLib.timeout_add(FADE_OUT_MS + 400, self._force_finish)
        return False

    def _force_finish(self) -> bool:
        self._stop_id = 0
        self._finish()
        return False

    def _finish(self) -> bool:
        if self._stop_id:
            GLib.source_remove(self._stop_id)
            self._stop_id = 0
        self.set_visible(False)
        return False

    def _cancel(self) -> None:
        for attribute in ("_hide_id", "_stop_id"):
            source = getattr(self, attribute)
            if source:
                GLib.source_remove(source)
                setattr(self, attribute, 0)
        if self._animation is not None:
            self._animation.pause()
            self._animation = None

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        self._cancel()
