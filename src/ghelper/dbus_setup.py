# SPDX-License-Identifier: GPL-3.0-or-later

"""Attach a GLib main loop and thread locks to dbus-python's shared buses, once.

dbus-python caches one connection per bus for the whole process, and a
connection created before a main loop is set can never export objects or
receive D-Bus signals.  The shared connection is also accessed from both the
GLib main loop and the background worker thread, so GLib thread support for
dbus-glib must be initialized at the same time.  Importing this module is a
deliberate side effect.
"""

from dbus.mainloop.glib import DBusGMainLoop, threads_init

threads_init()
DBusGMainLoop(set_as_default=True)
