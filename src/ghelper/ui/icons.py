# SPDX-License-Identifier: GPL-3.0-or-later

"""Theme-aware PNG icons.

GTK's symbolic SVG renderer flattens stroked line art, so the icons are
shipped as 96 px PNGs and loaded through the icon theme, which scales them
correctly for the widget size and HiDPI scale factor.
"""

from __future__ import annotations

import weakref

from ..deps import Adw, Gtk


class ThemedIcon(Gtk.Image):
    _instances: "weakref.WeakSet[ThemedIcon]" = weakref.WeakSet()
    _hooked = False

    def __init__(self, name: str, size: int = 16) -> None:
        super().__init__()
        self._name = name.removesuffix("-symbolic")
        self._size = size
        self.set_pixel_size(size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        ThemedIcon._instances.add(self)
        if not ThemedIcon._hooked:
            Adw.StyleManager.get_default().connect(
                "notify::dark", ThemedIcon._on_dark
            )
            ThemedIcon._hooked = True
        self.refresh()

    @classmethod
    def _on_dark(cls, *_args) -> None:
        cls.refresh_all()

    @classmethod
    def refresh_all(cls) -> None:
        for icon in list(cls._instances):
            icon.refresh()

    def set_icon(self, name: str) -> None:
        self._name = name.removesuffix("-symbolic")
        self.refresh()

    def refresh(self) -> None:
        variant = "dark" if Adw.StyleManager.get_default().get_dark() else "light"
        self.set_from_icon_name(f"{self._name}-{variant}")
        self.set_pixel_size(self._size)
