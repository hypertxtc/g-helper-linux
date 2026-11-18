# SPDX-License-Identifier: GPL-3.0-or-later

"""Learn and map Fn / ROG keys to G-Helper actions."""

from __future__ import annotations

from ..backend import hotkeys as hotkeys_mod
from ..deps import Adw, Gtk, Pango
from .theme import register_root
from .widgets import flat_button, dropdown


class HotkeysWindow(Adw.Window):
    def __init__(self, window) -> None:
        super().__init__(
            transient_for=window,
            modal=False,
            title="Hotkeys",
            default_width=560,
            default_height=380,
        )
        self.window = window
        self.settings = window.settings
        self._rows: dict[int, Gtk.DropDown] = {}
        self._actions: dict[str, str] = dict(self.settings.get("fn_actions") or {})
        self._listener = None

        register_root(self)

        header = Adw.HeaderBar()
        header.add_css_class("gh-header")
        title = Gtk.Label(label="Hotkeys")
        title.add_css_class("gh-app-title")
        header.pack_start(title)
        header.set_title_widget(Gtk.Label(label=""))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_margin_top(10)
        body.set_margin_bottom(12)
        body.set_margin_start(12)
        body.set_margin_end(12)

        intro = Gtk.Label(
            label="Press a key on the laptop keyboard and choose what G-Helper "
            "should do. Reading is passive, so the key still does whatever it "
            "normally does.",
            xalign=0.0,
            wrap=True,
        )
        intro.add_css_class("gh-live")
        body.append(intro)

        self._status = Gtk.Label(label="", xalign=0.0, wrap=True)
        self._status.add_css_class("gh-live")
        body.append(self._status)

        mapped = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="Mapped keys", xalign=0.0)
        title.add_css_class("gh-section-title")
        title.set_hexpand(True)
        mapped.append(title)
        clear = flat_button("Clear all", small=True)
        clear.connect("clicked", lambda *_: self.clear_all())
        mapped.append(clear)
        body.append(mapped)

        self._empty = Gtk.Label(label="No keys mapped yet \u2014 press one.", xalign=0.0)
        self._empty.add_css_class("gh-live")
        body.append(self._empty)

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("gh-scroll")
        scrolled.set_vexpand(True)
        scrolled.set_child(self._list)
        body.append(scrolled)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(body)
        self.set_content(toolbar)

        self.connect("destroy", self._on_destroy)

        # Do not run the main listener while learning, or pressing a key would
        # both open this window and trigger its action.
        self.window.stop_hotkeys()
        self._start_listening()

        for code in sorted(self._actions, key=_as_int):
            self._add_row(int(code))

    # -- listening --------------------------------------------------------
    def _start_listening(self) -> None:
        listener = hotkeys_mod.HotkeyListener(self._on_key)
        try:
            devices = listener.start()
        except hotkeys_mod.HotkeyError:
            self._status.set_text(hotkeys_mod.HINT)
            self._status.add_css_class("gh-status-error")
            return
        self._listener = listener
        names = ", ".join(dict.fromkeys(device.name for device in devices))
        self._status.set_text(f"Listening on {names}.")

    def _on_key(self, event) -> None:
        if event.code not in self._rows:
            self._add_row(event.code)

    # -- rows -------------------------------------------------------------
    def _add_row(self, code: int) -> None:
        if code in self._rows:
            return
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(3)
        row.set_margin_bottom(3)
        label = Gtk.Label(
            label=f"{hotkeys_mod.key_label(code)}  \u00b7  "
            f"{hotkeys_mod.key_name(code)} ({code})",
            xalign=0.0,
        )
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        combo = dropdown([hotkeys_mod.ACTIONS[action] for action in hotkeys_mod.ACTION_ORDER])
        combo.set_size_request(210, -1)
        combo.set_hexpand(False)

        current = self._actions.get(str(code), "ignore")
        values = list(hotkeys_mod.ACTION_ORDER)
        combo.set_selected(values.index(current) if current in values else values.index("ignore"))
        combo.connect("notify::selected", self._on_action_changed, code)

        row.append(label)
        row.append(combo)
        self._list.append(row)
        self._rows[code] = combo
        self._empty.set_visible(False)

    def _on_action_changed(self, combo: Gtk.DropDown, _param, code: int) -> None:
        index = combo.get_selected()
        if not (0 <= index < len(hotkeys_mod.ACTION_ORDER)):
            return
        action = hotkeys_mod.ACTION_ORDER[index]
        if action == "ignore":
            self._actions.pop(str(code), None)
        else:
            self._actions[str(code)] = action
        self.settings.set("fn_actions", dict(self._actions))

    def clear_all(self) -> None:
        self._actions.clear()
        self.settings.set("fn_actions", {})
        for widget in list(self._rows.values()):
            parent = widget.get_parent()
            if parent is not None:
                self._list.remove(parent)
        self._rows.clear()
        self._empty.set_visible(True)
        self._status.set_text("All mappings cleared. Press a key to add one.")

    # -- teardown ---------------------------------------------------------
    def _on_destroy(self, *_args) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.window.start_hotkeys()
        self.window.forget_hotkeys_window(self)


def _as_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
