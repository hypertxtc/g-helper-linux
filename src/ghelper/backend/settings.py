# SPDX-License-Identifier: GPL-3.0-or-later

"""Small JSON store for UI preferences that are not owned by asusd."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Bumped when the meaning of a stored value changes.
SETTINGS_VERSION = 2

DEFAULTS: dict[str, Any] = {
    "gpu_auto": False,
    "screen_auto": False,
    "auto_apply_ppt": False,
    "auto_apply_fans": False,
    "start_hidden": False,
    "window_width": 430,
    "window_height": 600,
    "window_maximized": False,
    "fn_enabled": True,
    "fn_actions": {"148": "toggle_window"},
    "gpu_apps": {},
    "settings_version": SETTINGS_VERSION,
}


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ghelper-linux" / "ui.json"


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, dict):
        if not isinstance(value, dict):
            return dict(default)
        return {str(k): str(v) for k, v in value.items() if str(v)}
    return value


class Settings:
    def __init__(self) -> None:
        self._values = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        file = path()
        if not file.exists():
            return
        try:
            with open(file, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read %s: %s", file, exc)
            return
        version = 1
        if isinstance(raw, dict):
            version = raw.get("settings_version", 1)
            for key in DEFAULTS:
                if key in raw:
                    self._values[key] = _coerce(key, raw[key])
        self._migrate(version)

    def _migrate(self, version: Any) -> None:
        if _coerce("settings_version", version) >= SETTINGS_VERSION:
            return
        actions = dict(self._values.get("fn_actions") or {})
        # v1 assumed the ROG key was KEY_PROG3.  On the GA402RK it is
        # KEY_PROG1, so move the old default instead of leaving a dead mapping
        # behind (and a stray one on whichever key actually sends PROG3).
        if actions.get("202") == "toggle_window" and "148" not in actions:
            actions.pop("202")
            actions["148"] = "toggle_window"
            self._values["fn_actions"] = actions
        self._values["settings_version"] = SETTINGS_VERSION
        self._save()

    def _save(self) -> None:
        file = path()
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(self._values, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("could not write %s: %s", file, exc)

    def get(self, key: str) -> Any:
        return self._values.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS:
            log.debug("ignoring unknown setting %s", key)
            return
        value = _coerce(key, value)
        if self._values.get(key) == value:
            return
        self._values[key] = value
        self._save()
