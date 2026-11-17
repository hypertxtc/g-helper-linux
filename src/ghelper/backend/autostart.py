# SPDX-License-Identifier: GPL-3.0-or-later

"""Run-on-startup toggle via an XDG autostart entry."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

FILENAME = "ghelper-linux.desktop"


def _autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / FILENAME


def _command(hidden: bool = False) -> str:
    flag = " --minimized" if hidden else ""
    installed = shutil.which("ghelper-linux")
    if installed:
        return f"{installed}{flag}"
    source_root = Path(__file__).resolve().parents[2]
    return (
        f'env PYTHONPATH="{source_root}" "{sys.executable}" -m ghelper{flag}'
    )


def enabled() -> bool:
    return _autostart_path().exists()


def set_enabled(value: bool, *, hidden: bool = False) -> None:
    path = _autostart_path()
    if not value:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not remove autostart entry: %s", exc)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=G-Helper\n"
        "Comment=ASUS laptop control\n"
        f"Exec={_command(hidden)}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n",
        encoding="utf-8",
    )
