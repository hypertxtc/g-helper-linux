# SPDX-License-Identifier: GPL-3.0-or-later

"""Wrapper around the cardwire CLI / cardwired daemon.

Cardwire blocks GPU device nodes at the eBPF-LSM level, which is the Linux
answer to G-Helper's Eco / Optimized GPU modes.  It does not replace the
firmware MUX switch, so both are exposed in the UI.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

MODE_LABELS = {
    "integrated": "Integrated",
    "hybrid": "Hybrid",
    "smart": "Smart",
    "manual": "Manual",
}

MODE_DESCRIPTIONS = {
    "integrated": "Only the integrated GPU is available. Best battery life.",
    "hybrid": "Both GPUs are available. The default for gaming on AC.",
    "smart": "The discrete GPU stays blocked; allow apps individually with cardwire launch.",
    "manual": "Per-GPU control from the list below.",
}


class CardwireError(RuntimeError):
    """Raised when the cardwire CLI is missing or fails."""


def available() -> bool:
    return shutil.which("cardwire") is not None


def _run(*args: str, timeout: float = 8.0) -> str:
    if not available():
        raise CardwireError("cardwire is not installed.")
    env = dict(os.environ, LC_ALL="C")
    try:
        proc = subprocess.run(
            ["cardwire", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CardwireError(f"cardwire {args[0]} timed out") from exc
    except OSError as exc:
        raise CardwireError(str(exc)) from exc
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise CardwireError(message[-1] if message else f"cardwire {args[0]} failed")
    return proc.stdout


def daemon_alive() -> bool:
    if not available():
        return False
    try:
        _run("manager", "status", timeout=4)
        return True
    except CardwireError:
        return False


def parse_state(output: str) -> dict[str, Any]:
    """Parse ``cardwire get`` output."""
    current: str | None = None
    modes: list[str] = []
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key.startswith("current"):
            current = value.lower()
        elif key.startswith("available"):
            modes = [m.strip().lower() for m in value.split(",") if m.strip()]
    return {"current": current, "available": modes}


def get_state() -> dict[str, Any]:
    """Return ``{"current": "hybrid", "available": [...]}``.

    ``current`` is None when the daemon is not reachable (for example during
    shutdown) so the UI can show an honest "unknown" instead of a fake mode.
    """
    return parse_state(_run("get"))


def set_mode(mode: str) -> None:
    _run("set", mode)


def parse_gpus(output: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CardwireError("cardwire returned invalid JSON") from exc
    if isinstance(raw, dict):
        gpus = list(raw.values())
    else:
        gpus = list(raw)
    return [g for g in gpus if isinstance(g, dict)]


def list_gpus() -> list[dict[str, Any]]:
    return parse_gpus(_run("list", "--json"))


def refresh_gpus() -> None:
    """Ask the daemon to re-detect GPUs after a PCI rescan."""
    _run("debug", "refresh-gpu", timeout=10)


def gpu_power(gpu_id: int | str) -> str | None:
    try:
        out = _run("gpu", "--power", str(gpu_id), timeout=4).strip()
    except CardwireError:
        return None
    return out or None


def set_blocked(gpu_id: int | str, blocked: bool) -> None:
    _run("gpu", "--block" if blocked else "--unblock", str(gpu_id))


def launch_on(gpu_id: int | str, command: list[str]) -> None:
    if not command:
        raise CardwireError("No command given.")
    _run("launch", "--gpu", str(gpu_id), "--", *command)


def parse_battery_auto_switch(output: str) -> bool | None:
    """Parse ``cardwire config battery-auto-switch`` output."""
    for line in output.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() != "batteryautoswitch":
            continue
        text = value.strip().lower()
        if text in ("true", "1", "on", "enabled"):
            return True
        if text in ("false", "0", "off", "disabled"):
            return False
    return None


def battery_auto_switch() -> bool | None:
    """Whether cardwire switches GPU modes on AC/battery by itself.

    ``None`` means cardwire is missing or did not answer, so callers can fall
    back to their own automation instead of assuming either way.
    """
    if not available():
        return None
    try:
        output = _run("config", "battery-auto-switch", timeout=4)
    except CardwireError:
        return None
    return parse_battery_auto_switch(output)


#: Launcher processes we started; reaped by :func:`reap`.
_spawned: list[subprocess.Popen] = []


def spawn(command: list[str], gpu: int | str | None = None) -> subprocess.Popen:
    """Start a program with the dGPU available, without waiting for it.

    Used by the per-app launcher: the child runs in its own session, so
    quitting G-Helper does not take the application down with it.
    """
    if not available():
        raise CardwireError("cardwire is not installed.")
    if not command:
        raise CardwireError("No command given.")
    argv = ["cardwire", "launch"]
    if gpu is not None:
        argv += ["--gpu", str(gpu)]
    argv += ["--", *command]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(os.environ),
        )
    except OSError as exc:
        raise CardwireError(str(exc)) from exc
    _spawned.append(proc)
    return proc


def reap() -> None:
    """Collect launcher processes that have finished, so they do not linger."""
    global _spawned
    _spawned = [proc for proc in _spawned if proc.poll() is None]
