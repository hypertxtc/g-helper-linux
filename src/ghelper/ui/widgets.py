# SPDX-License-Identifier: GPL-3.0-or-later

"""Small building blocks for the G-Helper-style interface."""

from __future__ import annotations

from typing import Callable, Iterable

from ..deps import GLib, Gtk, Pango
from .icons import ThemedIcon


class SectionHeader(Gtk.Box):
    def __init__(self, icon_name: str, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_css_class("gh-section-head")
        self._icon = ThemedIcon(icon_name, 16)
        self._icon.add_css_class("gh-section-icon")
        self._title = Gtk.Label(label=title, xalign=0.0)
        self._title.add_css_class("gh-section-title")
        self._live = Gtk.Label(label="", xalign=1.0)
        self._live.add_css_class("gh-live")
        self._live.set_hexpand(True)
        self._live.set_halign(Gtk.Align.END)
        self._suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.append(self._icon)
        self.append(self._title)
        self.append(self._live)
        self.append(self._suffix)

    def set_title(self, text: str) -> None:
        self._title.set_text(text)

    def set_live(self, text: str) -> None:
        self._live.set_text(text)

    def add_suffix(self, widget: Gtk.Widget) -> None:
        self._suffix.append(widget)


class Section(Gtk.Box):
    def __init__(self, icon_name: str, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("gh-section")
        self.header = SectionHeader(icon_name, title)
        self.append(self.header)

    def set_title(self, text: str) -> None:
        self.header.set_title(text)

    def set_live(self, text: str) -> None:
        self.header.set_live(text)

    def add(self, widget: Gtk.Widget) -> None:
        self.append(widget)


class ModeButton(Gtk.Button):
    def __init__(self, icon_name: str, label: str, accent: str = "blue") -> None:
        super().__init__()
        self.add_css_class("gh-mode-button")
        self.add_css_class(f"accent-{accent}")
        self.set_hexpand(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        self._icon = ThemedIcon(icon_name, 18)
        self._icon.add_css_class("gh-mode-icon")
        self._label = Gtk.Label(label=label)
        self._label.add_css_class("gh-mode-label")
        box.append(self._icon)
        box.append(self._label)
        self.set_child(box)

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")


def flat_button(label: str, *, secondary: bool = False, small: bool = False) -> Gtk.Button:
    button = Gtk.Button(label=label)
    button.add_css_class("gh-button")
    if secondary:
        button.add_css_class("secondary")
    if small:
        button.add_css_class("small")
    return button


def dropdown(items: Iterable[str]) -> Gtk.DropDown:
    widget = Gtk.DropDown.new_from_strings(list(items))
    widget.add_css_class("gh-dropdown")
    widget.set_hexpand(True)
    return widget


def set_selected(button: Gtk.Button, selected: bool) -> None:
    if selected:
        button.add_css_class("selected")
    else:
        button.remove_css_class("selected")


class ValueRow(Gtk.Box):
    def __init__(self, title: str, value: str = "—") -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_margin_top(3)
        self.set_margin_bottom(3)
        label = Gtk.Label(label=title, xalign=0.0)
        label.set_hexpand(True)
        self._value = Gtk.Label(label=value, xalign=1.0)
        self._value.add_css_class("gh-live")
        self.append(label)
        self.append(self._value)

    def set_value(self, text: str) -> None:
        self._value.set_text(text)


class ToggleRow(Gtk.Box):
    def __init__(self, title: str, active: bool = False, subtitle: str | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_margin_top(3)
        self.set_margin_bottom(3)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        labels.set_hexpand(True)
        label = Gtk.Label(label=title, xalign=0.0)
        labels.append(label)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0.0)
            sub.add_css_class("gh-live")
            sub.set_wrap(True)
            labels.append(sub)
        self.switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
        self.append(labels)
        self.append(self.switch)

    def get_active(self) -> bool:
        return self.switch.get_active()

    def set_active(self, active: bool) -> None:
        self.switch.set_active(active)


class BatteryScale(Gtk.Box):
    def __init__(self, on_change: Callable[[int], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._on_change = on_change
        self._timeout = 0
        self._blocked = False
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 20, 100, 5)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.add_css_class("gh-scale")
        for value in (20, 40, 60, 80, 100):
            self.scale.add_mark(
                value,
                Gtk.PositionType.BOTTOM,
                f"{value}" if value in (20, 60, 100) else None,
            )
        self.scale.connect("value-changed", self._changed)
        self.append(self.scale)

    def set_value(self, value: int) -> None:
        if abs(self.scale.get_value() - value) < 0.01:
            return
        self._blocked = True
        self.scale.set_value(value)
        self._blocked = False

    def _changed(self, scale: Gtk.Scale) -> None:
        if self._blocked:
            return
        if self._timeout:
            GLib.source_remove(self._timeout)
        self._timeout = GLib.timeout_add(300, self._commit)

    def _commit(self) -> bool:
        self._timeout = 0
        self._on_change(int(round(self.scale.get_value())))
        return False


class VerticalSlider(Gtk.Box):
    def __init__(
        self,
        title: str,
        lower: float,
        upper: float,
        value: float,
        on_change: Callable[[float], None],
        unit: str = "W",
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.set_halign(Gtk.Align.CENTER)
        self._on_change = on_change
        self._unit = unit
        self._timeout = 0
        self._blocked = False
        title_label = Gtk.Label(label=title)
        title_label.add_css_class("gh-live")
        self._value_label = Gtk.Label(label="")
        self._value_label.add_css_class("gh-big-value")
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, lower, upper, 1)
        self.scale.set_inverted(True)
        self.scale.set_draw_value(False)
        self.scale.set_size_request(-1, 180)
        self.scale.set_hexpand(False)
        self.scale.set_vexpand(True)
        self.scale.add_css_class("gh-scale")
        self.scale.add_css_class("vertical")
        self.scale.set_value(value)
        self.scale.connect("value-changed", self._changed)
        self.append(title_label)
        self.append(self._value_label)
        self.append(self.scale)
        self._rebuild_marks()
        self._refresh_label()

    def _refresh_label(self) -> None:
        self._value_label.set_text(f"{self.scale.get_value():.0f}{self._unit}")

    def _changed(self, _scale: Gtk.Scale) -> None:
        self._refresh_label()
        if self._blocked:
            return
        if self._timeout:
            GLib.source_remove(self._timeout)
        self._timeout = GLib.timeout_add(300, self._commit)

    def _commit(self) -> bool:
        self._timeout = 0
        self._on_change(self.scale.get_value())
        return False

    def get_value(self) -> float:
        return self.scale.get_value()

    def set_value(self, value: float) -> None:
        self._blocked = True
        self.scale.set_value(value)
        self._blocked = False
        self._refresh_label()

    def set_range(self, lower: float, upper: float) -> None:
        if (
            abs(self.scale.get_adjustment().get_lower() - lower) > 0.01
            or abs(self.scale.get_adjustment().get_upper() - upper) > 0.01
        ):
            self.scale.set_range(lower, upper)
            self._rebuild_marks()
        self._refresh_label()

    def _rebuild_marks(self) -> None:
        self.scale.clear_marks()
        adjustment = self.scale.get_adjustment()
        lower = adjustment.get_lower()
        upper = adjustment.get_upper()
        if upper <= lower:
            return
        for index in range(1, 8):
            value = lower + (upper - lower) * index / 8
            self.scale.add_mark(value, Gtk.PositionType.RIGHT, None)


def ellipsize(label: Gtk.Label) -> None:
    label.set_ellipsize(Pango.EllipsizeMode.END)
