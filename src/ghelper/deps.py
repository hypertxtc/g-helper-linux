# SPDX-License-Identifier: GPL-3.0-or-later

"""Single place that pins the GTK/libadwaita versions.

Importing gi.repository widgets from multiple modules without pinning the
versions first is a common source of confusing warnings, so every UI module
imports its toolkit names from here.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402,F401
