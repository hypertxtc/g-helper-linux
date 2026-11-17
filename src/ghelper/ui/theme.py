# SPDX-License-Identifier: GPL-3.0-or-later

"""Theme setup: icon search path, stylesheet and dark-mode bookkeeping."""

from __future__ import annotations

import weakref
from pathlib import Path

from ..deps import Adw, Gdk, Gtk

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ICON_DIR = DATA_DIR / "icons"

_roots: "weakref.WeakSet[Gtk.Widget]" = weakref.WeakSet()
_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    theme = Gtk.IconTheme.get_for_display(display)
    theme.add_search_path(str(ICON_DIR))

    css = DATA_DIR / "style.css"
    if css.exists():
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    Adw.StyleManager.get_default().connect("notify::dark", lambda *_: _refresh())
    _installed = True


def register_root(widget: Gtk.Widget) -> None:
    widget.add_css_class("gh-window")
    _roots.add(widget)
    _apply(widget)


def _refresh() -> None:
    for root in list(_roots):
        _apply(root)


def _apply(widget: Gtk.Widget) -> None:
    if Adw.StyleManager.get_default().get_dark():
        widget.add_css_class("dark")
    else:
        widget.remove_css_class("dark")


def is_dark() -> bool:
    return bool(Adw.StyleManager.get_default().get_dark())
