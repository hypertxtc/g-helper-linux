# SPDX-License-Identifier: GPL-3.0-or-later

"""Optional power-source automation daemon.

asusd already switches performance profiles on AC/battery changes.  This
small user service adds the two things it does not do:

* refresh-rate automation (KDE, via kscreen-doctor)
* cardwire GPU-mode automation (integrated on battery, hybrid on AC)

It is opt-in: install the unit, edit ``~/.config/ghelper-linux/automation.json``
and enable ``ghelper-automation.service``.  Manual cardwire modes (smart /
manual) are respected and never overridden.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

from .backend import cardwire, display
from .backend.system import ac_online

log = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    # This is the G-Helper "Optimized" behaviour and is safe without reboots.
    "gpu_mode": True,
    "gpu_on_battery": "integrated",
    "gpu_on_ac": "hybrid",
    # Off by default: some desktops (notably KWin) ask for confirmation when
    # the mode changes, which is annoying for an automatic switch.
    "refresh_rate": False,
    "refresh_on_battery": 60,
    "refresh_on_ac": "max",
    "poll_seconds": 5,
}

VALID_GPU_MODES = ("integrated", "hybrid", "smart", "manual")


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ghelper-linux" / "automation.json"


def merge_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return defaults merged with user values, with sanity checks."""
    config = dict(DEFAULT_CONFIG)
    if not raw:
        return config
    for key, value in raw.items():
        if key in config:
            config[key] = value

    if config["gpu_on_battery"] not in VALID_GPU_MODES:
        config["gpu_on_battery"] = DEFAULT_CONFIG["gpu_on_battery"]
    if config["gpu_on_ac"] not in VALID_GPU_MODES:
        config["gpu_on_ac"] = DEFAULT_CONFIG["gpu_on_ac"]
    if not isinstance(config["refresh_on_battery"], (int, float)):
        config["refresh_on_battery"] = DEFAULT_CONFIG["refresh_on_battery"]
    if config["refresh_on_ac"] != "max":
        try:
            config["refresh_on_ac"] = float(config["refresh_on_ac"])
        except (TypeError, ValueError):
            config["refresh_on_ac"] = DEFAULT_CONFIG["refresh_on_ac"]
    try:
        config["poll_seconds"] = max(2, int(config["poll_seconds"]))
    except (TypeError, ValueError):
        config["poll_seconds"] = DEFAULT_CONFIG["poll_seconds"]
    return config


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return merge_config(None)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read %s: %s (using defaults)", path, exc)
        return merge_config(None)
    return merge_config(raw if isinstance(raw, dict) else None)


def write_default_config() -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
    return path


def gpu_target(config: dict[str, Any], ac: bool) -> str:
    return config["gpu_on_ac"] if ac else config["gpu_on_battery"]


def refresh_target(
    config: dict[str, Any], ac: bool, rates: list[float]
) -> float | None:
    if not rates:
        return None
    target = config["refresh_on_ac"] if ac else config["refresh_on_battery"]
    if target == "max":
        return max(rates)
    try:
        value = float(target)
    except (TypeError, ValueError):
        return None
    return value if any(abs(value - rate) < 0.3 for rate in rates) else None


class Automation:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._gpu_manual = False
        self._stop = False

    def stop(self, *_args) -> None:
        self._stop = True

    def apply(self, ac: bool, *, dry_run: bool = False) -> list[str]:
        actions: list[str] = []
        if self.config["gpu_mode"] and not self._gpu_manual:
            actions.extend(self._apply_gpu(ac, dry_run))
        if self.config["refresh_rate"]:
            actions.extend(self._apply_refresh(ac, dry_run))
        return actions

    def _apply_gpu(self, ac: bool, dry_run: bool) -> list[str]:
        target = gpu_target(self.config, ac)
        if not cardwire.available():
            return []
        try:
            current = (cardwire.get_state().get("current") or "").lower()
        except cardwire.CardwireError as exc:
            log.warning("cardwire: %s", exc)
            return []
        if current in ("smart", "manual"):
            self._gpu_manual = True
            log.info("GPU is in %s mode; leaving it alone", current)
            return []
        if current == target:
            return []
        action = f"GPU mode: {current or 'unknown'} -> {target}"
        if not dry_run:
            try:
                cardwire.set_mode(target)
            except cardwire.CardwireError as exc:
                log.warning("could not set GPU mode: %s", exc)
                return []
        return [action]

    def _apply_refresh(self, ac: bool, dry_run: bool) -> list[str]:
        try:
            panel = display.primary_panel()
        except display.DisplayError as exc:
            log.warning("display: %s", exc)
            return []
        if panel is None:
            return []
        mode = display.current_mode(panel)
        if mode is None:
            return []
        rates = display.refresh_rates(panel, mode)
        target = refresh_target(self.config, ac, rates)
        if target is None or abs(mode["rate"] - target) < 0.3:
            return []
        action = f"refresh rate: {mode['rate']:g} Hz -> {target:g} Hz"
        if not dry_run:
            try:
                display.set_refresh_rate(
                    panel["name"], mode["width"], mode["height"], target
                )
            except display.DisplayError as exc:
                log.warning("could not set refresh rate: %s", exc)
                return []
        return [action]

    def run(self, *, once: bool = False, dry_run: bool = False) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        last_ac: bool | None = None
        log.info(
            "automation started (gpu=%s refresh=%s dry_run=%s)",
            self.config["gpu_mode"],
            self.config["refresh_rate"],
            dry_run,
        )
        while not self._stop:
            ac = ac_online()
            if ac is not None and (last_ac is None or ac != last_ac):
                source = "AC" if ac else "battery"
                for action in self.apply(ac, dry_run=dry_run):
                    log.info("[%s] %s", source, action)
                last_ac = ac
            if once:
                break
            slept = 0.0
            interval = float(self.config["poll_seconds"])
            while slept < interval and not self._stop:
                time.sleep(min(0.5, interval - slept))
                slept += 0.5
        log.info("automation stopped")
        return 0


def run_daemon(*, once: bool = False, dry_run: bool = False) -> int:
    config = load_config()
    return Automation(config).run(once=once, dry_run=dry_run)
