# SPDX-License-Identifier: GPL-3.0-or-later

"""Tray icon via StatusNotifierItem + com.canonical.dbusmenu.

KDE Plasma supports this natively; GNOME needs an extension.  Failures are
non-fatal: the application simply runs without a tray icon.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

from . import dbus_setup  # noqa: F401 - the export below needs a main loop
from .backend import asusd as asusd_mod

log = logging.getLogger(__name__)

SNI_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
BUS_NAME = "io.github.ghelperlinux.GHelper.Tray"

ITEM_SHOW = 1
ITEM_SEP1 = 2
ITEM_QUIET = 3
ITEM_BALANCED = 4
ITEM_PERFORMANCE = 5
ITEM_SEP2 = 6
ITEM_QUIT = 7
ITEM_ECO = 8
ITEM_STANDARD = 9
ITEM_SMART = 10
ITEM_OPTIMIZED = 11
ITEM_SEP3 = 12

PROFILE_BY_ITEM = {
    ITEM_QUIET: asusd_mod.QUIET,
    ITEM_BALANCED: asusd_mod.BALANCED,
    ITEM_PERFORMANCE: asusd_mod.PERFORMANCE,
}

GPU_BY_ITEM = {
    ITEM_ECO: "eco",
    ITEM_STANDARD: "standard",
    ITEM_SMART: "smart",
    ITEM_OPTIMIZED: "optimized",
}


def _argb_pixmap(path: Path, size: int = 32) -> dbus.Struct | None:
    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except (ImportError, ValueError):
        return None
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), size, size, True)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not load tray icon: %s", exc)
        return None
    width, height = pixbuf.get_width(), pixbuf.get_height()
    channels = pixbuf.get_n_channels()
    stride = pixbuf.get_rowstride()
    data = pixbuf.get_pixels()
    out = bytearray()
    for y in range(height):
        row = y * stride
        for x in range(width):
            offset = row + x * channels
            red, green, blue = data[offset], data[offset + 1], data[offset + 2]
            alpha = data[offset + 3] if channels == 4 else 255
            out += bytes((alpha, red, green, blue))
    return dbus.Struct(
        (
            dbus.Int32(width),
            dbus.Int32(height),
            dbus.Array(out, signature="y"),
        ),
        signature="iiay",
    )


class _TrayIcon(dbus.service.Object):
    def __init__(self, bus_name: dbus.service.BusName, tray: "Tray") -> None:
        super().__init__(bus_name, SNI_PATH)
        self._tray = tray

    def _properties(self) -> dict:
        subtitle = self._tray.tooltip_body or (
            f"{asusd_mod.PROFILE_NAMES.get(self._tray.profile, '')} mode"
        )
        return {
            "Category": dbus.String("ApplicationStatus"),
            "Id": dbus.String("ghelper-linux"),
            "Title": dbus.String("g-helper-linux"),
            "Status": dbus.String("Active"),
            "IconName": dbus.String("io.github.ghelperlinux.GHelper"),
            "IconPixmap": dbus.Array(self._tray.icon_pixmaps, signature="(iiay)"),
            "ToolTip": dbus.Struct(
                (
                    dbus.String(""),
                    dbus.Array([], signature="(iiay)"),
                    dbus.String(self._tray.tooltip_title),
                    dbus.String(subtitle),
                ),
                signature="sa(iiay)ss",
            ),
            "ItemIsMenu": dbus.Boolean(False),
            "Menu": dbus.ObjectPath(MENU_PATH),
        }

    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, interface: str, name: str):
        return self._properties().get(name, dbus.String(""))

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str):
        return dbus.Dictionary(self._properties(), signature="sv")

    @dbus.service.method(PROPS_IFACE, in_signature="ssv", out_signature="")
    def Set(self, interface: str, name: str, value) -> None:
        return None

    @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="")
    def Activate(self, x: int, y: int) -> None:
        self._tray.show()

    @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="")
    def SecondaryActivate(self, x: int, y: int) -> None:
        self._tray.show()

    @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="")
    def ContextMenu(self, x: int, y: int) -> None:
        return None

    @dbus.service.method(SNI_IFACE, in_signature="is", out_signature="")
    def Scroll(self, delta: int, orientation: str) -> None:
        return None

    @dbus.service.signal(SNI_IFACE, signature="")
    def NewToolTip(self) -> None:
        return None


class _TrayMenu(dbus.service.Object):
    def __init__(self, bus_name: dbus.service.BusName, tray: "Tray") -> None:
        super().__init__(bus_name, MENU_PATH)
        self._tray = tray

    def _item(self, item_id: int, props: dict, children: list | None = None):
        return dbus.Struct(
            (
                dbus.Int32(item_id),
                dbus.Dictionary(
                    {key: value for key, value in props.items()}, signature="sv"
                ),
                dbus.Array(children or [], signature="v"),
            ),
            signature="ia{sv}av",
        )

    def _text(self, label: str, enabled: bool = True) -> dict:
        return {
            "label": dbus.String(label),
            "enabled": dbus.Boolean(enabled),
            "visible": dbus.Boolean(True),
        }

    def _radio(self, label: str, active: bool, group: int) -> dict:
        props = self._text(label)
        props["toggle-type"] = dbus.String("radio")
        props["toggle-state"] = dbus.Int32(1 if active else 0)
        # dbusmenu uses toggle-group to keep the profile and GPU radios from
        # unchecking each other: one radio group per section.
        props["toggle-group"] = dbus.Int32(group)
        return props

    def _separator(self) -> dict:
        return {
            "type": dbus.String("separator"),
            "visible": dbus.Boolean(True),
        }

    def _layout(self) -> tuple:
        current = self._tray.profile
        gpu = self._tray.gpu_state
        children = [
            self._item(ITEM_SHOW, self._text("Open G-Helper")),
            self._item(ITEM_SEP1, self._separator()),
            self._item(
                ITEM_QUIET, self._radio("Silent", current == asusd_mod.QUIET, 1)
            ),
            self._item(
                ITEM_BALANCED,
                self._radio("Balanced", current == asusd_mod.BALANCED, 1),
            ),
            self._item(
                ITEM_PERFORMANCE,
                self._radio("Turbo", current == asusd_mod.PERFORMANCE, 1),
            ),
            self._item(ITEM_SEP2, self._separator()),
            self._item(ITEM_ECO, self._radio("Eco", gpu == "eco", 2)),
            self._item(ITEM_STANDARD, self._radio("Standard", gpu == "standard", 2)),
            self._item(ITEM_SMART, self._radio("Smart", gpu == "smart", 2)),
            self._item(
                ITEM_OPTIMIZED, self._radio("Optimized", gpu == "optimized", 2)
            ),
            self._item(ITEM_SEP3, self._separator()),
            self._item(ITEM_QUIT, self._text("Quit")),
        ]
        children = [
            dbus.Struct(child, signature="ia{sv}av", variant_level=1) for child in children
        ]
        root = self._item(0, {"children-display": dbus.String("submenu")}, children)
        return dbus.Struct((dbus.UInt32(0), root), signature="u(ia{sv}av)")

    @dbus.service.method(MENU_IFACE, in_signature="iias", out_signature="u(ia{sv}av)")
    def GetLayout(self, parent_id: int, recursion_depth: int, property_names):
        return self._layout()

    @dbus.service.method(MENU_IFACE, in_signature="aias", out_signature="a(ia{sv})")
    def GetGroupProperties(self, ids, property_names):
        layout = self._layout()
        root = layout[1]
        children = [child for child in root[2]]
        wanted = [int(i) for i in ids]
        result = []
        if 0 in wanted:
            result.append(dbus.Struct((dbus.Int32(0), root[1]), signature="ia{sv}"))
        for child in children:
            if wanted and int(child[0]) not in wanted:
                continue
            result.append(dbus.Struct((dbus.Int32(child[0]), child[1]), signature="ia{sv}"))
        return dbus.Array(result, signature="(ia{sv})")

    @dbus.service.method(MENU_IFACE, in_signature="is", out_signature="v")
    def GetProperty(self, item_id: int, name: str):
        for child in self._layout()[1][2]:
            if int(child[0]) == int(item_id):
                return child[1].get(name, dbus.String(""))
        return dbus.String("")

    @dbus.service.method(MENU_IFACE, in_signature="isvu", out_signature="")
    def Event(self, item_id: int, event_id: str, data, timestamp: int) -> None:
        if str(event_id) != "clicked":
            return
        ident = int(item_id)
        if ident == ITEM_SHOW:
            self._tray.show()
        elif ident == ITEM_QUIT:
            self._tray.quit()
        elif ident in PROFILE_BY_ITEM:
            self._tray.select_profile(PROFILE_BY_ITEM[ident])
        elif ident in GPU_BY_ITEM:
            self._tray.select_gpu(GPU_BY_ITEM[ident])

    @dbus.service.method(MENU_IFACE, in_signature="i", out_signature="b")
    def AboutToShow(self, item_id: int) -> bool:
        return True

    @dbus.service.method(MENU_IFACE, in_signature="ai", out_signature="aiai")
    def AboutToShowGroup(self, ids):
        return dbus.Array([], signature="i"), dbus.Array([], signature="i")

    @dbus.service.signal(MENU_IFACE, signature="ui")
    def LayoutUpdated(self, revision: int, parent: int) -> None:
        return None


class Tray:
    """Owns the SNI objects and presents a small menu."""

    def __init__(
        self,
        *,
        icon_path: Path,
        profile: int,
        gpu_state: str | None,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
        on_profile: Callable[[int], None],
        on_gpu: Callable[[str], None],
    ) -> None:
        self.active = False
        self.profile = profile
        self.gpu_state = gpu_state
        self.tooltip_title = "G-Helper"
        self.tooltip_body = ""
        self._on_show = on_show
        self._on_quit = on_quit
        self._on_profile = on_profile
        self._on_gpu = on_gpu

        pixmap = _argb_pixmap(icon_path)
        self.icon_pixmaps = [pixmap] if pixmap is not None else []

        bus = dbus.SessionBus(mainloop=DBusGMainLoop())
        watcher_obj = bus.get_object(WATCHER_BUS, WATCHER_PATH, introspect=False)
        self._watcher = dbus.Interface(watcher_obj, WATCHER_BUS)

        self._bus_name = dbus.service.BusName(BUS_NAME, bus)
        self._icon = _TrayIcon(self._bus_name, self)
        self._menu = _TrayMenu(self._bus_name, self)
        self._watcher.RegisterStatusNotifierItem(BUS_NAME, dbus_interface=WATCHER_BUS)
        self.active = True
        log.info("tray icon registered with %s", WATCHER_BUS)

    # -- callbacks ------------------------------------------------------
    def show(self) -> None:
        self._on_show()

    def quit(self) -> None:
        self._on_quit()

    def select_profile(self, profile: int) -> None:
        self._on_profile(profile)

    def select_gpu(self, mode: str) -> None:
        self._on_gpu(mode)

    def set_gpu_state(self, state: str | None) -> None:
        if state == self.gpu_state:
            return
        self.gpu_state = state
        try:
            self._menu.LayoutUpdated(1, 0)
        except Exception as exc:  # noqa: BLE001
            log.debug("tray GPU update failed: %s", exc)

    def set_profile(self, profile: int) -> None:
        if profile == self.profile:
            return
        self.profile = profile
        try:
            self._menu.LayoutUpdated(1, 0)
            self._icon.NewToolTip()
        except Exception as exc:  # noqa: BLE001
            log.debug("tray update failed: %s", exc)

    def set_tooltip(self, body: str, title: str | None = None) -> None:
        """Show live status (temperatures, profile) under the icon."""
        new_title = title or self.tooltip_title
        if body == self.tooltip_body and new_title == self.tooltip_title:
            return
        self.tooltip_body = body
        self.tooltip_title = new_title
        try:
            self._icon.NewToolTip()
        except Exception as exc:  # noqa: BLE001
            log.debug("tray tooltip update failed: %s", exc)
