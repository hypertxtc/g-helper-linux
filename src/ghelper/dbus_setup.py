# SPDX-License-Identifier: GPL-3.0-or-later

"""Attach a GLib main loop to dbus-python's shared connections, once.

dbus-python caches one connection per bus for the whole process, and a
connection created before a main loop is set stays loop-less forever.  The tray
exports a StatusNotifierItem on the session bus, and exporting fails with
"connections must be attached to a main loop" if anything opened the bus first.

That used to be safe by accident: the tray module was imported before any other
code touched the session bus.  Startup work added later — the KWin placement
helper — can now run first, so the setup lives in its own module and is
imported by the entry point, the tray and the KWin helper.  Importing it is a
deliberate side effect.
"""

from dbus.mainloop.glib import DBusGMainLoop

DBusGMainLoop(set_as_default=True)
