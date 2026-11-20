# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared D-Bus introspection helpers for optional asusd interfaces."""

from __future__ import annotations

from collections import deque
from typing import Any
import xml.etree.ElementTree as ET

import dbus


def parse_introspect(xml_text: str, iface: str) -> tuple[bool, dict[str, str], list[str]]:
    """Parse introspection XML for ``iface`` properties and child node names."""
    root = ET.fromstring(xml_text)
    children = [
        name for node in root.findall("node") if (name := node.get("name"))
    ]
    for element in root.findall("interface"):
        if element.get("name") == iface:
            prop_types = {
                name: prop_type
                for prop in element.findall("property")
                if (name := prop.get("name")) and (prop_type := prop.get("type"))
            }
            return True, prop_types, children
    return False, {}, children


def infer_dbus_signature(value: Any) -> str | None:
    """Infer a D-Bus type signature from a ``dbus-python`` value."""
    if isinstance(value, dbus.Boolean):
        return "b"
    if isinstance(value, dbus.Byte):
        return "y"
    if isinstance(value, dbus.UInt32):
        return "u"
    if isinstance(value, dbus.Int32):
        return "i"
    if isinstance(value, dbus.Double):
        return "d"
    if isinstance(value, dbus.String):
        return "s"
    if isinstance(value, (dbus.Struct, tuple, list)) and len(value) == 4:
        if all(isinstance(item, dbus.Byte) for item in value):
            return "(yyyy)"
        if all(isinstance(item, dbus.UInt32) for item in value):
            return "(uuuu)"
        if all(isinstance(item, dbus.Int32) for item in value):
            return "(iiii)"
        if all(isinstance(item, dbus.String) for item in value):
            return "(ssss)"
    return None


def discover_interface(
    bus: Any,
    bus_name: str,
    candidate_paths: tuple[str, ...],
    iface: str,
    timeout: float = 10.0,
) -> tuple[str, dict[str, str]] | None:
    """Locate the object path exposing ``iface`` and return ``(path, prop_types)``."""
    try:
        if not bus.name_has_owner(bus_name):
            return None
    except dbus.DBusException:
        return None

    hint = iface.rsplit(".", 1)[-1].lower()
    queue: deque[tuple[str, int]] = deque((path, 0) for path in candidate_paths)
    visited: set[str] = set()

    while queue:
        path, depth = queue.popleft()
        if path in visited:
            continue
        visited.add(path)
        try:
            obj = bus.get_object(bus_name, path, introspect=False)
            intro = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
            xml_text = intro.Introspect(timeout=timeout)
            found, prop_types, children = parse_introspect(xml_text, iface)
        except (dbus.DBusException, ET.ParseError):
            continue
        if found:
            return path, prop_types
        if depth >= 2:
            continue
        for child in children:
            if path == "/xyz/ljones" and hint not in child.lower():
                continue
            child_path = f"{path.rstrip('/')}/{child.lstrip('/')}"
            if child_path not in visited:
                queue.append((child_path, depth + 1))
    return None
