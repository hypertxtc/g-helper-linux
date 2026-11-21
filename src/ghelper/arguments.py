# SPDX-License-Identifier: GPL-3.0-or-later

"""Command-line options that are forwarded between application instances."""

from __future__ import annotations

PAGES = ("main", "fans", "settings", "hotkeys", "setup")


def parse_gui_arguments(arguments: list[str]) -> tuple[str, bool]:
    """Return ``(page, hidden)`` from a local or forwarded command line."""
    page = "main"
    hidden = False
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument.startswith("--page="):
            candidate = argument.split("=", 1)[1]
            if candidate in PAGES:
                page = candidate
            index += 1
            continue
        if argument in ("--page", "-p"):
            if index + 1 < len(arguments) and arguments[index + 1] in PAGES:
                page = arguments[index + 1]
            index += 2
            continue
        if argument in ("--minimized", "--hidden", "-m"):
            hidden = True
        index += 1
    return page, hidden


def is_quit_requested(arguments: list[str]) -> bool:
    """Return True when ``--quit`` was passed to stop a running instance."""
    return any(arg == "--quit" for arg in arguments[1:])
