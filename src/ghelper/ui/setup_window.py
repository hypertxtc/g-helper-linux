# SPDX-License-Identifier: GPL-3.0-or-later

"""First-run onboarding and prerequisite setup check window."""

from __future__ import annotations

from ..backend import setup_check
from ..backend.setup_check import CheckItem
from ..deps import Adw, Gtk
from .theme import register_root
from .widgets import flat_button

_STATUS_BADGE = {
    "ok": ("Ready", "gh-status-ok"),
    "warn": ("Action needed", "gh-status-warn"),
    "error": ("Missing", "gh-status-error"),
    "info": ("Info", "gh-live"),
}

_STATUS_CLASSES = ("gh-status-ok", "gh-status-warn", "gh-status-error")


class SetupWindow(Adw.Window):
    def __init__(self, window, initial_items: list[CheckItem] | None = None) -> None:
        super().__init__(
            transient_for=window,
            modal=False,
            title="Setup Check",
            default_width=500,
            default_height=580,
        )
        self.window = window
        self._refresh_pending = False
        self._busy_action = False
        self._destroyed = False
        self._items: list[CheckItem] = []
        self._fix_buttons: list[Gtk.Button] = []
        self.connect("destroy", self._on_destroy)

        register_root(self)

        header = Adw.HeaderBar()
        header.add_css_class("gh-header")
        title = Gtk.Label(label="Setup Check")
        title.add_css_class("gh-app-title")
        header.pack_start(title)
        header.set_title_widget(Gtk.Label(label=""))

        summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        summary_box.set_margin_top(10)
        summary_box.set_margin_bottom(6)
        summary_box.set_margin_start(12)
        summary_box.set_margin_end(12)

        self._headline = Gtk.Label(label="Checking system setup\u2026", xalign=0.0, wrap=True)
        self._headline.add_css_class("gh-section-title")
        self._subtitle = Gtk.Label(label="", xalign=0.0, wrap=True)
        self._subtitle.add_css_class("gh-live")
        summary_box.append(self._headline)
        summary_box.append(self._subtitle)

        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._list_box.set_margin_top(4)
        self._list_box.set_margin_bottom(10)
        self._list_box.set_margin_start(12)
        self._list_box.set_margin_end(12)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("gh-scroll")
        scrolled.set_vexpand(True)
        scrolled.set_child(self._list_box)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        footer.set_margin_top(6)
        footer.set_margin_bottom(10)
        footer.set_margin_start(12)
        footer.set_margin_end(12)

        self._recheck_button = flat_button("Re-check", secondary=True)
        self._recheck_button.connect("clicked", lambda *_: self.refresh())
        self._copy_button = flat_button("Copy diagnostic report", secondary=True)
        self._copy_button.connect(
            "clicked", lambda *_: self.window.copy_diagnostics(self._copy_button)
        )
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self._done_button = flat_button("Done")
        self._done_button.connect("clicked", lambda *_: self._on_done())

        footer.append(self._recheck_button)
        footer.append(self._copy_button)
        footer.append(spacer)
        footer.append(self._done_button)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        content.append(summary_box)
        content.append(scrolled)
        content.append(footer)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(content)
        self.set_content(toolbar)

        if initial_items is not None:
            self._apply(list(initial_items))
        else:
            self.refresh()

    # -- data & UI --------------------------------------------------------
    def refresh(self) -> None:
        if self._refresh_pending or self._destroyed:
            return
        self._refresh_pending = True
        self._recheck_button.set_sensitive(False)
        self.window.run(
            setup_check.collect_checks,
            done=self._apply,
            error=self._load_failed,
        )

    def _load_failed(self, exc: Exception) -> None:
        self._refresh_pending = False
        if self._destroyed:
            return
        self._recheck_button.set_sensitive(True)
        self.window.show_error(exc)

    def _apply(self, items: list[CheckItem]) -> None:
        self._refresh_pending = False
        if self._destroyed:
            return
        self._items = list(items)
        self._recheck_button.set_sensitive(not self._busy_action)

        status_key, headline, subtitle = setup_check.summary_state(items)
        self._headline.set_text(headline)
        self._subtitle.set_text(subtitle)
        for cls in _STATUS_CLASSES:
            self._headline.remove_css_class(cls)
        if status_key in ("ok", "warn", "error"):
            self._headline.add_css_class(f"gh-status-{status_key}")

        self._rebuild_rows(items)

    def _rebuild_rows(self, items: list[CheckItem]) -> None:
        child = self._list_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._list_box.remove(child)
            child = following
        self._fix_buttons.clear()

        for item in items:
            self._list_box.append(self._build_card(item))

    def _build_card(self, item: CheckItem) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("gh-setup-card")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=item.title, xalign=0.0, wrap=True)
        title.add_css_class("gh-setup-title")
        title.set_hexpand(True)

        badge_text, badge_cls = _STATUS_BADGE.get(item.status, ("Info", "gh-live"))
        badge = Gtk.Label(label=badge_text, xalign=1.0)
        badge.add_css_class(badge_cls)
        badge.add_css_class("gh-status")

        top.append(title)
        top.append(badge)

        if item.fix_action and item.fix_label:
            button = flat_button(item.fix_label, small=True)
            button.set_sensitive(not self._busy_action)
            button.connect("clicked", lambda _btn, it=item: self._run_fix(it))
            self._fix_buttons.append(button)
            top.append(button)

        card.append(top)

        summary = Gtk.Label(label=item.summary, xalign=0.0, wrap=True)
        summary.add_css_class("gh-value")
        card.append(summary)

        if item.detail:
            detail = Gtk.Label(label=item.detail, xalign=0.0, wrap=True)
            detail.add_css_class("gh-live")
            card.append(detail)

        return card

    # -- actions ----------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        self._busy_action = busy
        self._recheck_button.set_sensitive(not busy and not self._refresh_pending)
        for button in self._fix_buttons:
            button.set_sensitive(not busy)

    def _run_fix(self, item: CheckItem) -> None:
        action = item.fix_action
        if not action or self._busy_action:
            return
        self._set_busy(True)

        def done(_result) -> None:
            if self._destroyed:
                return
            self._set_busy(False)
            if action == "enable-bpf-lsm":
                self.window.toast("GRUB updated — reboot once to enable BPF LSM")
            else:
                self.window.toast(f"Completed: {item.fix_label or action}")
            self.window.refresh_all()
            self.refresh()

        def failed(exc: Exception) -> None:
            if self._destroyed:
                return
            self._set_busy(False)
            self.window.show_error(exc)

        self.window.run(lambda: setup_check.run_fix(action), done=done, error=failed)

    def _on_done(self) -> None:
        self.window.settings.set("onboarding_seen", True)
        self.close()

    def _on_destroy(self, *_args) -> None:
        self._destroyed = True
        self.window.settings.set("onboarding_seen", True)
        self.window.forget_setup_window(self)
