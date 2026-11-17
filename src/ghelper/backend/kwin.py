# SPDX-License-Identifier: GPL-3.0-or-later

"""Place a window next to the KDE panel through KWin's scripting interface.

Wayland does not let an application position its own windows, so on Plasma we
ask KWin to move the window into the corner next to the taskbar.  Every failure
is non-fatal: the window simply stays wherever KWin put it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading

import dbus

log = logging.getLogger(__name__)

BUS_NAME = "org.kde.KWin"
SCRIPTING_PATH = "/Scripting"
SCRIPTING_IFACE = "org.kde.kwin.Scripting"
SCRIPT_IFACE = "org.kde.kwin.Script"

SCRIPT = """
const caption = {caption};
const margin = {margin};

const windows = (typeof workspace.windowList === "function")
    ? workspace.windowList()
    : workspace.clientList();
const desktop = workspace.currentDesktop;

for (const window of windows) {{
    if (String(window.caption) !== caption) continue;

    const full = workspace.clientArea(KWin.FullScreenArea, window.output, desktop);
    const work = workspace.clientArea(KWin.PlacementArea, window.output, desktop);
    if (!full || !work) continue;

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
}}
"""


def available() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if "kde" not in desktop:
        return False
    try:
        bus = dbus.SessionBus()
        return bool(bus.name_has_owner(BUS_NAME))
    except dbus.DBusException:
        return False


def place_window(caption: str = "G-Helper", *, margin: int = 8) -> bool:
    """Move the window with ``caption`` next to the panel. Returns success."""
    if not available():
        return False

    handle = tempfile.NamedTemporaryFile(
        "w", prefix="ghelper-kwin-", suffix=".js", delete=False
    )
    path = handle.name
    name = f"ghelper-place-{os.getpid()}"
    script_id = -1
    try:
        handle.write(SCRIPT.format(caption=json.dumps(caption), margin=margin))
        handle.close()

        bus = dbus.SessionBus()
        scripting = dbus.Interface(
            bus.get_object(BUS_NAME, SCRIPTING_PATH, introspect=False), SCRIPTING_IFACE
        )
        script_id = int(scripting.loadScript(path, name))
        if script_id < 0:
            log.debug("KWin refused to load the placement script")
            return False
        script = dbus.Interface(
            bus.get_object(
                BUS_NAME, f"/Scripting/Script{script_id}", introspect=False
            ),
            SCRIPT_IFACE,
        )
        script.run()
        return True
    except dbus.DBusException as exc:
        log.debug("KWin placement failed: %s", exc)
        return False
    finally:
        try:
            if not handle.closed:
                handle.close()
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        if script_id >= 0:
            timer = threading.Timer(1.0, _unload, args=(name,))
            timer.daemon = True
            timer.start()


def _unload(name: str) -> None:
    try:
        bus = dbus.SessionBus()
        scripting = dbus.Interface(
            bus.get_object(BUS_NAME, SCRIPTING_PATH, introspect=False), SCRIPTING_IFACE
        )
        scripting.unloadScript(name)
    except dbus.DBusException as exc:
        log.debug("could not unload the KWin script: %s", exc)
