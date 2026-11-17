# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep the window next to the KDE panel through KWin's scripting interface.

Wayland does not let an application position its own windows: KWin maps a new
window wherever its placement policy says (usually the centre of the screen)
and can only move it afterwards.  Moving it after the fact is what makes the
window visibly jump.

Instead we install a small, persistent KWin script for the lifetime of the
application.  It listens for ``workspace.windowAdded`` — the signal KWin emits
while adding a window, before it is painted for the first time — and sets the
final geometry there, the same place KWin's own window rules act.  The first
frame is therefore already in the right corner.  Re-running the script moves
windows that are already mapped, which is used when the panel or screen layout
changes.

Every failure is non-fatal: the window simply stays wherever KWin put it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

import dbus

log = logging.getLogger(__name__)

BUS_NAME = "org.kde.KWin"
SCRIPTING_PATH = "/Scripting"
SCRIPTING_IFACE = "org.kde.kwin.Scripting"
SCRIPT_IFACE = "org.kde.kwin.Script"

SCRIPT_NAME = "ghelper-placement"

PLACEMENT_SCRIPT = """
const resourceClass = {resource_class};
const caption = {caption};
const margin = {margin};

function isTarget(window) {{
    if (resourceClass) return String(window.resourceClass) === resourceClass;
    return caption !== "" && String(window.caption) === caption;
}}

function place(window) {{
    if (!isTarget(window)) return;

    try {{
        // Wayland clients do not carry a per-window desktop, so use the current one.
        const desktop = workspace.currentDesktop;
        const full = workspace.clientArea(KWin.FullScreenArea, window.output, desktop);
        const work = workspace.clientArea(KWin.PlacementArea, window.output, desktop);
        if (!full || !work) return;

        const insetTop = work.y - full.y;
        const insetBottom = (full.y + full.height) - (work.y + work.height);
        const insetLeft = work.x - full.x;
        const insetRight = (full.x + full.width) - (work.x + work.width);

        const geometry = Object.assign({{}}, window.frameGeometry);
        const width = geometry.width;
        const height = geometry.height;

        if (Math.max(insetLeft, insetRight) > Math.max(insetTop, insetBottom)) {{
            // Vertical panel: sit beside it, aligned to the bottom.
            geometry.x = (insetRight >= insetLeft)
                ? work.x + work.width - width - margin
                : work.x + margin;
            geometry.y = work.y + work.height - height - margin;
        }} else if (insetTop > insetBottom) {{
            // Panel at the top.
            geometry.x = work.x + work.width - width - margin;
            geometry.y = work.y + margin;
        }} else {{
            // Panel at the bottom (or hidden).
            geometry.x = work.x + work.width - width - margin;
            geometry.y = work.y + work.height - height - margin;
        }}

        window.frameGeometry = geometry;
    }} catch (error) {{
        // A window without an output yet must not break the watcher.
    }}
}}

if (typeof __ghelperPlacement === "undefined") {{
    __ghelperPlacement = true;
    workspace.windowAdded.connect(place);
}}

// Also correct windows that are already mapped.
const windows = (typeof workspace.windowList === "function")
    ? workspace.windowList()
    : workspace.clientList();
for (const window of windows) place(window);
"""

_script_id: int | None = None
_script_path: str | None = None


def available() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" not in desktop:
        return False
    try:
        bus = dbus.SessionBus()
        return bool(bus.name_has_owner(BUS_NAME))
    except dbus.DBusException:
        return False


def _interface(path: str, iface: str):
    bus = dbus.SessionBus()
    return dbus.Interface(bus.get_object(BUS_NAME, path, introspect=False), iface)


def install(
    caption: str = "G-Helper",
    resource_class: str | None = None,
    *,
    margin: int = 8,
) -> bool:
    """Load the placement watcher. Call before the window is first mapped."""
    global _script_id, _script_path

    if _script_id is not None:
        return True
    if not available():
        return False

    handle = tempfile.NamedTemporaryFile(
        "w", prefix="ghelper-kwin-", suffix=".js", delete=False, encoding="utf-8"
    )
    path = handle.name
    try:
        handle.write(
            PLACEMENT_SCRIPT.format(
                resource_class=json.dumps(resource_class or ""),
                caption=json.dumps(caption or ""),
                margin=margin,
            )
        )
        handle.close()

        scripting = _interface(SCRIPTING_PATH, SCRIPTING_IFACE)
        try:
            # A previous instance may have died without unloading its script.
            scripting.unloadScript(SCRIPT_NAME)
        except dbus.DBusException:
            pass
        script_id = int(scripting.loadScript(path, SCRIPT_NAME))
        if script_id < 0:
            log.debug("KWin refused to load the placement script")
            os.unlink(path)
            return False

        _script_id = script_id
        _script_path = path
        _interface(f"/Scripting/Script{script_id}", SCRIPT_IFACE).run()
        log.debug("placement watcher installed (script %s)", script_id)
        return True
    except (dbus.DBusException, OSError) as exc:
        log.debug("KWin placement failed: %s", exc)
        try:
            handle.close()
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        return False


def place_window(
    caption: str = "G-Helper",
    resource_class: str | None = None,
    *,
    margin: int = 8,
) -> bool:
    """Install the watcher if needed, then reposition mapped windows."""
    if not install(caption, resource_class, margin=margin):
        return False
    if _script_id is None:
        return False
    try:
        _interface(f"/Scripting/Script{_script_id}", SCRIPT_IFACE).run()
        return True
    except dbus.DBusException as exc:
        log.debug("KWin placement failed: %s", exc)
        return False


def stop() -> None:
    """Unload the watcher so it does not outlive the application."""
    global _script_id, _script_path

    if _script_id is None:
        return
    try:
        _interface(SCRIPTING_PATH, SCRIPTING_IFACE).unloadScript(SCRIPT_NAME)
    except dbus.DBusException as exc:
        log.debug("could not unload the KWin script: %s", exc)
    _script_id = None
    if _script_path is not None:
        try:
            os.unlink(_script_path)
        except OSError:
            pass
        _script_path = None
