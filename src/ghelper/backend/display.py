# SPDX-License-Identifier: GPL-3.0-or-later

"""Display helpers across KDE Plasma, GNOME, Hyprland and wlroots compositors.

Refresh-rate switching is desktop specific:

* KDE Plasma (Wayland / X11): ``kscreen-doctor``
* GNOME (Mutter): ``org.gnome.Mutter.DisplayConfig`` over session D-Bus
* Hyprland: ``hyprctl`` (or ``wlr-randr`` as a fallback)
* wlroots compositors (sway, wayfire, river, labwc, niri, ...): ``wlr-randr``

On unsupported desktops or when the session's display tool is missing, callers
degrade cleanly to read-only instead of spawning an incompatible tool.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

import dbus

from .. import dbus_setup  # noqa: F401 - attach the main loop before any bus

log = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MODE = re.compile(r"(\d+):(\d+)x(\d+)@([\d.]+)([*!]*)")
_GEOMETRY = re.compile(r"(\d+)x(\d+)")
_HYPR_MODE = re.compile(r"^(\d+)x(\d+)@([\d.]+)(?:Hz)?$")
_WLR_TEXT_MODE = re.compile(
    r"^(\d+)x(\d+)\s+px,\s+([\d.]+)\s+Hz(?:\s+\(([^)]+)\))?$"
)

MUTTER_BUS = "org.gnome.Mutter.DisplayConfig"
MUTTER_PATH = "/org/gnome/Mutter/DisplayConfig"
MUTTER_IFACE = "org.gnome.Mutter.DisplayConfig"
MUTTER_METHOD_TEMPORARY = 1
DBUS_TIMEOUT = 10.0

WLROOTS_DESKTOPS = frozenset(
    {"SWAY", "WAYFIRE", "RIVER", "LABWC", "NIRI", "WLROOTS", "COSMIC"}
)


class DisplayError(RuntimeError):
    pass


# -- desktop detection ---------------------------------------------------
def desktop_tokens() -> set[str]:
    """Desktop names from the session environment, normalised to upper case."""
    value = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get(
        "XDG_SESSION_DESKTOP", ""
    )
    return {token.strip().upper() for token in re.split(r"[:;]", value) if token.strip()}


def is_kde() -> bool:
    return "KDE" in desktop_tokens()


def is_gnome() -> bool:
    return any(t == "GNOME" or t.startswith("GNOME-") for t in desktop_tokens())


def is_hyprland() -> bool:
    return "HYPRLAND" in desktop_tokens() or bool(
        os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    )


def is_wlroots() -> bool:
    return bool(desktop_tokens() & WLROOTS_DESKTOPS) or bool(os.environ.get("SWAYSOCK"))


def _is_panel_name(name: str) -> bool:
    return name.lower().startswith(("edp", "lvds", "dsi"))


def _mutter_available() -> bool:
    try:
        return bool(dbus.SessionBus().name_has_owner(MUTTER_BUS))
    except Exception:  # noqa: BLE001
        return False


def backend_name() -> str | None:
    """Return the active display backend key, or ``None`` when unavailable."""
    if is_kde():
        return "kscreen" if shutil.which("kscreen-doctor") else None
    if is_gnome():
        return "mutter" if _mutter_available() else None
    if is_hyprland():
        if shutil.which("hyprctl"):
            return "hyprland"
        if shutil.which("wlr-randr"):
            return "wlr-randr"
        return None
    if is_wlroots():
        return "wlr-randr" if shutil.which("wlr-randr") else None
    if not desktop_tokens():
        if shutil.which("kscreen-doctor"):
            return "kscreen"
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            return "hyprland"
        if shutil.which("wlr-randr"):
            return "wlr-randr"
        if _mutter_available():
            return "mutter"
        return None
    return None


def available() -> bool:
    """Whether refresh-rate control can work in the current session."""
    return backend_name() is not None


def unavailable_reason() -> str:
    """Short, user-facing explanation for a disabled refresh row."""
    if is_kde() and not shutil.which("kscreen-doctor"):
        return "Install kscreen-doctor to switch refresh rates"
    if is_gnome():
        return "GNOME Mutter DisplayConfig service is not available"
    if is_hyprland():
        return "Install hyprctl or wlr-randr to switch refresh rates"
    if is_wlroots():
        return "Install wlr-randr to switch refresh rates"
    if not desktop_tokens():
        return "No supported display backend found (KDE, GNOME or wlroots)"
    return "Refresh-rate switching needs KDE Plasma, GNOME or a wlroots compositor"


# -- KDE / kscreen-doctor ------------------------------------------------
def parse_outputs(text: str) -> list[dict[str, Any]]:
    """Parse ``kscreen-doctor -o`` output (ANSI escapes tolerated)."""
    result: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = _ANSI.sub("", raw_line).strip()
        if line.startswith("Output:"):
            parts = line.split()
            current = {
                "name": parts[2] if len(parts) > 2 else "unknown",
                "enabled": False,
                "panel": False,
                "modes": [],
                "scale": None,
                "geometry": None,
            }
            result.append(current)
            continue
        if current is None:
            continue
        if line == "enabled":
            current["enabled"] = True
        elif line == "Panel":
            current["panel"] = True
        elif line.startswith("Modes:"):
            for match in _MODE.finditer(line):
                current["modes"].append(
                    {
                        "id": int(match.group(1)),
                        "width": int(match.group(2)),
                        "height": int(match.group(3)),
                        "rate": float(match.group(4)),
                        "current": "*" in match.group(5),
                        "preferred": "!" in match.group(5),
                    }
                )
        elif line.startswith("Scale:"):
            try:
                current["scale"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Geometry:"):
            match = _GEOMETRY.search(line)
            if match:
                current["geometry"] = (int(match.group(1)), int(match.group(2)))
    return result


def _run_tool(cmd: list[str], *, timeout: float, failure: str) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DisplayError(str(exc)) from exc
    if proc.returncode != 0:
        raise DisplayError((proc.stderr or proc.stdout or failure).strip())
    return proc.stdout


def _kscreen_outputs() -> list[dict[str, Any]]:
    return parse_outputs(
        _run_tool(["kscreen-doctor", "-o"], timeout=8, failure="kscreen-doctor failed")
    )


def _find_mode(
    outputs_list: list[dict[str, Any]],
    output_name: str,
    width: int,
    height: int,
    rate: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    output = next((o for o in outputs_list if o["name"] == output_name), None)
    if output is None:
        raise DisplayError(f"Output {output_name} not found")
    matches = [
        m
        for m in output["modes"]
        if m["width"] == width and m["height"] == height and abs(m["rate"] - rate) < 0.3
    ]
    if not matches:
        raise DisplayError(f"{width}x{height}@{rate:g} is not a valid mode")
    return output, matches[0]


def _kscreen_set_refresh_rate(
    output_name: str, width: int, height: int, rate: float
) -> None:
    _output, mode = _find_mode(_kscreen_outputs(), output_name, width, height, rate)
    _run_tool(
        ["kscreen-doctor", f"output.{output_name}.mode.{mode['id']}"],
        timeout=10,
        failure="Could not change refresh rate",
    )


# -- GNOME / Mutter D-Bus ------------------------------------------------
def parse_mutter_state(state: Any) -> list[dict[str, Any]]:
    """Parse ``org.gnome.Mutter.DisplayConfig.GetCurrentState`` return value."""
    _serial, monitors, logical_monitors, _props = state
    attached_connectors: set[str] = set()
    logical_scale: dict[str, float] = {}
    for logical in logical_monitors:
        scale = float(logical[2])
        for attached in logical[5]:
            connector = str(attached[0])
            attached_connectors.add(connector)
            logical_scale[connector] = scale

    result: list[dict[str, Any]] = []
    for monitor in monitors:
        ident, raw_modes, mon_props = monitor
        connector = str(ident[0])
        parsed_modes: list[dict[str, Any]] = []
        current_dims: tuple[int, int] | None = None
        for mode in raw_modes:
            mode_id, width, height, rate = str(mode[0]), int(mode[1]), int(mode[2]), float(mode[3])
            mode_props = mode[6] if len(mode) > 6 and isinstance(mode[6], dict) else {}
            is_current = bool(mode_props.get("is-current", False))
            is_preferred = bool(mode_props.get("is-preferred", False))
            if is_current:
                current_dims = (width, height)
            parsed_modes.append(
                {
                    "id": mode_id,
                    "width": width,
                    "height": height,
                    "rate": rate,
                    "current": is_current,
                    "preferred": is_preferred,
                }
            )
        is_builtin = bool(mon_props.get("is-builtin", False)) or _is_panel_name(connector)
        enabled = (connector in attached_connectors) or any(
            m["current"] for m in parsed_modes
        )
        result.append(
            {
                "name": connector,
                "enabled": enabled,
                "panel": is_builtin,
                "modes": parsed_modes,
                "scale": logical_scale.get(connector),
                "geometry": current_dims,
            }
        )
    return result


def build_mutter_apply_args(
    state: Any, output_name: str, width: int, height: int, rate: float
) -> tuple[int, int, list[tuple[Any, ...]], dict[str, int]]:
    """Build ``ApplyMonitorsConfig`` arguments from a ``GetCurrentState`` reply."""
    serial, monitors, logical_monitors, global_props = state

    target_monitor = next(
        (mon for mon in monitors if str(mon[0][0]) == output_name), None
    )
    if target_monitor is None:
        raise DisplayError(f"Output {output_name} not found")

    target_mode_id: str | None = None
    for mode in target_monitor[1]:
        if (
            int(mode[1]) == width
            and int(mode[2]) == height
            and abs(float(mode[3]) - rate) < 0.3
        ):
            target_mode_id = str(mode[0])
            break
    if target_mode_id is None:
        raise DisplayError(f"{width}x{height}@{rate:g} is not a valid mode")

    current_modes: dict[str, str] = {}
    for mon in monitors:
        connector = str(mon[0][0])
        for mode in mon[1]:
            mode_props = mode[6] if len(mode) > 6 and isinstance(mode[6], dict) else {}
            if mode_props.get("is-current", False):
                current_modes[connector] = str(mode[0])
                break

    logical_out: list[tuple[Any, ...]] = []
    found_in_logical = False
    for logical in logical_monitors:
        x = int(logical[0])
        y = int(logical[1])
        scale = float(logical[2])
        transform = int(logical[3])
        primary = bool(logical[4])
        mon_entries: list[tuple[str, str, dict[str, Any]]] = []
        for attached in logical[5]:
            connector = str(attached[0])
            if connector == output_name:
                found_in_logical = True
                mode_id: str | None = target_mode_id
            else:
                mode_id = current_modes.get(connector)
            if mode_id is not None:
                mon_entries.append((connector, mode_id, {}))
        if mon_entries:
            logical_out.append((x, y, scale, transform, primary, mon_entries))

    if not found_in_logical:
        raise DisplayError(f"Output {output_name} is not active")

    props_out: dict[str, int] = {}
    if (
        isinstance(global_props, dict)
        and global_props.get("supports-changing-layout-mode")
        and "layout-mode" in global_props
    ):
        props_out["layout-mode"] = int(global_props["layout-mode"])

    return int(serial), MUTTER_METHOD_TEMPORARY, logical_out, props_out


def _mutter_proxy():
    bus = dbus.SessionBus()
    obj = bus.get_object(MUTTER_BUS, MUTTER_PATH, introspect=False)
    return dbus.Interface(obj, MUTTER_IFACE)


def _mutter_outputs() -> list[dict[str, Any]]:
    try:
        state = _mutter_proxy().GetCurrentState(timeout=DBUS_TIMEOUT)
    except dbus.DBusException as exc:
        raise DisplayError(exc.get_dbus_message() or str(exc)) from exc
    return parse_mutter_state(state)


def _mutter_set_refresh_rate(
    output_name: str, width: int, height: int, rate: float
) -> None:
    try:
        proxy = _mutter_proxy()
        state = proxy.GetCurrentState(timeout=DBUS_TIMEOUT)
        serial, method, logical_out, props_out = build_mutter_apply_args(
            state, output_name, width, height, rate
        )
        dbus_logical = dbus.Array(
            [
                dbus.Struct(
                    (
                        dbus.Int32(x),
                        dbus.Int32(y),
                        dbus.Double(scale),
                        dbus.UInt32(transform),
                        dbus.Boolean(primary),
                        dbus.Array(
                            [
                                dbus.Struct(
                                    (
                                        dbus.String(conn),
                                        dbus.String(mode_id),
                                        dbus.Dictionary({}, signature="sv"),
                                    ),
                                    signature="ssa{sv}",
                                )
                                for conn, mode_id, _mprops in mon_entries
                            ],
                            signature="(ssa{sv})",
                        ),
                    ),
                    signature="iiduba(ssa{sv})",
                )
                for x, y, scale, transform, primary, mon_entries in logical_out
            ],
            signature="(iiduba(ssa{sv}))",
        )
        dbus_props = dbus.Dictionary(
            {
                dbus.String(k): dbus.UInt32(int(v), variant_level=1)
                for k, v in props_out.items()
            },
            signature="sv",
        )
        proxy.ApplyMonitorsConfig(
            dbus.UInt32(serial),
            dbus.UInt32(method),
            dbus_logical,
            dbus_props,
            timeout=DBUS_TIMEOUT,
        )
    except dbus.DBusException as exc:
        raise DisplayError(exc.get_dbus_message() or str(exc)) from exc


# -- Hyprland / hyprctl --------------------------------------------------
def parse_hyprland_outputs(text: str) -> list[dict[str, Any]]:
    """Parse ``hyprctl monitors -j`` JSON output."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DisplayError("hyprctl returned invalid JSON") from exc
    if not isinstance(raw, list):
        return []

    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        enabled = not bool(item.get("disabled", False))
        cur_w = int(item.get("width") or 0)
        cur_h = int(item.get("height") or 0)
        cur_rate = float(item.get("refreshRate") or 0.0)
        modes: list[dict[str, Any]] = []
        matched_current = False
        for idx, entry in enumerate(item.get("availableModes") or []):
            match = _HYPR_MODE.match(str(entry).strip())
            if not match:
                continue
            width = int(match.group(1))
            height = int(match.group(2))
            rate = float(match.group(3))
            is_current = (
                not matched_current
                and width == cur_w
                and height == cur_h
                and abs(rate - cur_rate) < 0.3
            )
            if is_current:
                matched_current = True
            modes.append(
                {
                    "id": idx,
                    "width": width,
                    "height": height,
                    "rate": rate,
                    "current": is_current,
                    "preferred": idx == 0,
                }
            )
        if not matched_current and cur_w > 0 and cur_h > 0 and cur_rate > 0:
            modes.append(
                {
                    "id": len(modes),
                    "width": cur_w,
                    "height": cur_h,
                    "rate": cur_rate,
                    "current": True,
                    "preferred": False,
                }
            )
        scale = float(item["scale"]) if item.get("scale") is not None else None
        geometry = (cur_w, cur_h) if cur_w > 0 and cur_h > 0 else None
        result.append(
            {
                "name": name,
                "enabled": enabled,
                "panel": _is_panel_name(name),
                "modes": modes,
                "scale": scale,
                "geometry": geometry,
                "_x": int(item.get("x") or 0),
                "_y": int(item.get("y") or 0),
                "_transform": int(item.get("transform") or 0),
            }
        )
    return result


def _hyprland_outputs() -> list[dict[str, Any]]:
    return parse_hyprland_outputs(
        _run_tool(["hyprctl", "monitors", "-j"], timeout=8, failure="hyprctl failed")
    )


def _hyprland_set_refresh_rate(
    output_name: str, width: int, height: int, rate: float
) -> None:
    output, mode = _find_mode(_hyprland_outputs(), output_name, width, height, rate)
    x = int(output.get("_x") or 0)
    y = int(output.get("_y") or 0)
    scale = output.get("scale") if output.get("scale") is not None else 1.0
    transform = int(output.get("_transform") or 0)
    rule = f"{output_name},{width}x{height}@{mode['rate']:.2f},{x}x{y},{scale:g}"
    if transform != 0:
        rule += f",transform,{transform}"
    _run_tool(
        ["hyprctl", "keyword", "monitor", rule],
        timeout=10,
        failure="Could not change refresh rate",
    )


# -- wlroots / wlr-randr -------------------------------------------------
def _normalize_refresh(value: float) -> float:
    if value > 1_000_000:
        return value / 1_000_000.0
    if value > 1000:
        return value / 1000.0
    return float(value)


def parse_wlr_outputs(text: str) -> list[dict[str, Any]]:
    """Parse ``wlr-randr --json`` output (both list and dict-keyed shapes)."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DisplayError("wlr-randr returned invalid JSON") from exc

    items: list[tuple[str | None, dict[str, Any]]] = []
    if isinstance(raw, list):
        items = [(None, entry) for entry in raw if isinstance(entry, dict)]
    elif isinstance(raw, dict):
        items = [(str(k), v) for k, v in raw.items() if isinstance(v, dict)]

    result: list[dict[str, Any]] = []
    for key_name, item in items:
        name = str(item.get("name") or key_name or "")
        if not name:
            continue
        modes: list[dict[str, Any]] = []
        for idx, mode in enumerate(item.get("modes") or []):
            if not isinstance(mode, dict):
                continue
            modes.append(
                {
                    "id": idx,
                    "width": int(mode.get("width") or 0),
                    "height": int(mode.get("height") or 0),
                    "rate": _normalize_refresh(float(mode.get("refresh") or 0.0)),
                    "current": bool(mode.get("current", False)),
                    "preferred": bool(mode.get("preferred", False)),
                }
            )
        if not any(m["current"] for m in modes) and isinstance(
            item.get("current_mode"), dict
        ):
            cm = item["current_mode"]
            cm_w = int(cm.get("width") or 0)
            cm_h = int(cm.get("height") or 0)
            cm_r = _normalize_refresh(float(cm.get("refresh") or 0.0))
            matched = False
            for m in modes:
                if (
                    m["width"] == cm_w
                    and m["height"] == cm_h
                    and abs(m["rate"] - cm_r) < 0.3
                ):
                    m["current"] = True
                    matched = True
                    break
            if not matched and cm_w > 0 and cm_h > 0 and cm_r > 0:
                modes.append(
                    {
                        "id": len(modes),
                        "width": cm_w,
                        "height": cm_h,
                        "rate": cm_r,
                        "current": True,
                        "preferred": False,
                    }
                )
        cur = next((m for m in modes if m["current"]), None)
        geometry = (cur["width"], cur["height"]) if cur else None
        scale = float(item["scale"]) if item.get("scale") is not None else None
        result.append(
            {
                "name": name,
                "enabled": bool(item.get("enabled", False)),
                "panel": _is_panel_name(name),
                "modes": modes,
                "scale": scale,
                "geometry": geometry,
            }
        )
    return result


def parse_wlr_text(text: str) -> list[dict[str, Any]]:
    """Fallback parser for plain-text ``wlr-randr`` output."""
    result: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        if raw_line and not raw_line[0].isspace():
            name = raw_line.split()[0]
            current = {
                "name": name,
                "enabled": False,
                "panel": _is_panel_name(name),
                "modes": [],
                "scale": None,
                "geometry": None,
            }
            result.append(current)
            continue
        if current is None:
            continue
        line = raw_line.strip()
        if line.startswith("Enabled:"):
            current["enabled"] = line.split(":", 1)[1].strip().lower() == "yes"
        elif line.startswith("Scale:"):
            try:
                current["scale"] = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        else:
            match = _WLR_TEXT_MODE.match(line)
            if match:
                width = int(match.group(1))
                height = int(match.group(2))
                rate = float(match.group(3))
                flags = (match.group(4) or "").lower()
                is_current = "current" in flags
                is_preferred = "preferred" in flags
                if is_current:
                    current["geometry"] = (width, height)
                current["modes"].append(
                    {
                        "id": len(current["modes"]),
                        "width": width,
                        "height": height,
                        "rate": rate,
                        "current": is_current,
                        "preferred": is_preferred,
                    }
                )
    return result


def _wlr_outputs() -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(
            ["wlr-randr", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DisplayError(str(exc)) from exc
    if proc.returncode == 0 and proc.stdout.strip():
        return parse_wlr_outputs(proc.stdout)
    return parse_wlr_text(
        _run_tool(["wlr-randr"], timeout=8, failure="wlr-randr failed")
    )


def _wlr_set_refresh_rate(
    output_name: str, width: int, height: int, rate: float
) -> None:
    _output, mode = _find_mode(_wlr_outputs(), output_name, width, height, rate)
    _run_tool(
        [
            "wlr-randr",
            "--output",
            output_name,
            "--mode",
            f"{width}x{height}@{mode['rate']:g}Hz",
        ],
        timeout=10,
        failure="Could not change refresh rate",
    )


# -- unified public API --------------------------------------------------
def outputs() -> list[dict[str, Any]]:
    backend = backend_name()
    if backend == "kscreen":
        return _kscreen_outputs()
    if backend == "mutter":
        return _mutter_outputs()
    if backend == "hyprland":
        return _hyprland_outputs()
    if backend == "wlr-randr":
        return _wlr_outputs()
    return []


def primary_panel() -> dict[str, Any] | None:
    candidates = [o for o in outputs() if o["enabled"]]
    for output in candidates:
        if output["panel"]:
            return output
    return candidates[0] if candidates else None


def current_mode(output: dict[str, Any]) -> dict[str, Any] | None:
    for mode in output["modes"]:
        if mode["current"]:
            return mode
    return None


def refresh_rates(output: dict[str, Any], mode: dict[str, Any] | None = None) -> list[float]:
    mode = mode or current_mode(output)
    if mode is None:
        return []
    rates = {
        m["rate"]
        for m in output["modes"]
        if m["width"] == mode["width"] and m["height"] == mode["height"]
    }
    return sorted(rates, reverse=True)


def set_refresh_rate(output_name: str, width: int, height: int, rate: float) -> None:
    backend = backend_name()
    if backend is None:
        raise DisplayError(unavailable_reason())
    if backend == "kscreen":
        _kscreen_set_refresh_rate(output_name, width, height, rate)
    elif backend == "mutter":
        _mutter_set_refresh_rate(output_name, width, height, rate)
    elif backend == "hyprland":
        _hyprland_set_refresh_rate(output_name, width, height, rate)
    elif backend == "wlr-randr":
        _wlr_set_refresh_rate(output_name, width, height, rate)
