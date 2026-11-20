# SPDX-License-Identifier: GPL-3.0-or-later

"""Named fan-curve presets and JSON import / export.

Presets are plain JSON next to ``ui.json`` so they are easy to back up, diff
and share::

    ~/.config/ghelper-linux/fan-curves.json

The same document shape is used for export files, which means an exported file
can be renamed to ``fan-curves.json`` to replace the whole library.

Curves coming from a file are validated before they reach asusd: eight
monotonic temperature points and eight fan values, exactly what the firmware
expects.  A malformed file must never be able to spin a fan to a silly value or
push an out-of-order curve into the embedded controller.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import settings as settings_mod

log = logging.getLogger(__name__)

VERSION = 1
FANS = ("CPU", "GPU", "MID")
POINTS = 8
PWM_MAX = 255
TEMP_MIN = 0
TEMP_MAX = 100
NAME_LIMIT = 60


class CurveError(ValueError):
    """Raised when a preset name, curve or file is malformed."""


def path() -> Path:
    """Location of the preset library."""
    return settings_mod.path().with_name("fan-curves.json")


# -- validation ---------------------------------------------------------
def _clean_curve(fan: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CurveError(f"{fan}: expected an object with temp and pwm lists")

    temps = raw.get("temp")
    pwm = raw.get("pwm")
    for key, values in (("temp", temps), ("pwm", pwm)):
        if not isinstance(values, (list, tuple)) or len(values) != POINTS:
            raise CurveError(f"{fan}: {key} needs exactly {POINTS} points")

    try:
        temps = [int(value) for value in temps]
        pwm = [int(value) for value in pwm]
    except (TypeError, ValueError) as exc:
        raise CurveError(f"{fan}: points must be whole numbers") from exc

    if any(not TEMP_MIN <= value <= TEMP_MAX for value in temps):
        raise CurveError(
            f"{fan}: temperatures must be between {TEMP_MIN} and {TEMP_MAX} \u00b0C"
        )
    if any(not 0 <= value <= PWM_MAX for value in pwm):
        raise CurveError(f"{fan}: fan values must be between 0 and {PWM_MAX}")
    if any(b <= a for a, b in zip(temps, temps[1:])):
        raise CurveError(f"{fan}: temperatures must increase from left to right")

    return {
        "temp": temps,
        "pwm": pwm,
        "enabled": bool(raw.get("enabled", True)),
    }


def normalize(curves: Any) -> dict[str, dict[str, Any]]:
    """Validate a ``{fan: curve}`` mapping and return a clean copy."""
    if not isinstance(curves, dict):
        raise CurveError("expected a mapping of fan name to curve")

    unknown = sorted(set(map(str, curves)) - set(FANS))
    if unknown:
        raise CurveError("unknown fan(s): " + ", ".join(unknown))

    cleaned: dict[str, dict[str, Any]] = {}
    for fan in FANS:
        if fan in curves:
            cleaned[fan] = _clean_curve(fan, curves[fan])
    if not cleaned:
        raise CurveError("no CPU, GPU or MID curve found")
    return cleaned


def clean_name(name: Any) -> str:
    """Collapse whitespace and enforce a sane length."""
    text = " ".join(str(name).split()) if name is not None else ""
    if not text:
        raise CurveError("give the preset a name")
    if len(text) > NAME_LIMIT:
        text = text[:NAME_LIMIT].rstrip()
    return text


def unique_name(base: str, existing: Iterable[str]) -> str:
    """Return ``base`` or ``base (2)``, ``base (3)``… when already taken."""
    taken = set(existing)
    if base not in taken:
        return base
    for index in range(2, 1000):
        candidate = f"{base} ({index})"
        if candidate not in taken:
            return candidate
    raise CurveError("too many presets with the same name")


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


# -- library --------------------------------------------------------------
def _entry(spec: Any, name: str) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise CurveError(f"{name}: expected an object")
    curves = spec.get("curves", spec)
    return {
        "created": str(spec.get("created") or ""),
        "curves": normalize(curves),
    }


def load() -> dict[str, dict[str, Any]]:
    """Read the preset library, skipping anything that does not validate."""
    file = path()
    if not file.exists():
        return {}
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read %s: %s", file, exc)
        return {}
    presets = raw.get("presets") if isinstance(raw, dict) else None
    if not isinstance(presets, dict):
        log.warning("%s does not contain a presets object", file)
        return {}

    library: dict[str, dict[str, Any]] = {}
    for name, spec in presets.items():
        try:
            library[str(name)] = _entry(spec, str(name))
        except CurveError as exc:
            log.warning("skipping preset %r: %s", name, exc)
    return library


def _document(presets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"version": VERSION, "presets": presets}


def save(presets: dict[str, dict[str, Any]]) -> None:
    """Write the library atomically, so a crash cannot truncate it."""
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + ".tmp")
    temporary.write_text(
        json.dumps(_document(presets), indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, file)


def add(
    name: Any,
    curves: Any,
    *,
    presets: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Store (or overwrite) a preset and persist the library."""
    library = load() if presets is None else presets
    name = clean_name(name)
    entry = {"created": _stamp(), "curves": normalize(curves)}
    library[name] = entry
    save(library)
    return name, entry


def remove(name: str) -> bool:
    """Delete a preset. Returns whether anything was removed."""
    library = load()
    if name not in library:
        return False
    del library[name]
    save(library)
    return True


# -- files ----------------------------------------------------------------
def export_file(target: str | os.PathLike[str], name: Any, curves: Any) -> str:
    """Write one preset to ``target``; returns the cleaned preset name."""
    name = clean_name(name)
    document = _document({name: {"created": _stamp(), "curves": normalize(curves)}})
    target = Path(target)
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return name


def _importable(raw: Any, fallback: str) -> list[tuple[str, Any]]:
    if not isinstance(raw, dict):
        raise CurveError("the file does not contain a JSON object")

    presets = raw.get("presets")
    if isinstance(presets, dict):
        return [(str(name), spec) for name, spec in presets.items()]
    if "curves" in raw:
        return [(str(raw.get("name") or fallback), raw)]
    raise CurveError("no fan curve presets in this file")


def import_file(
    source: str | os.PathLike[str],
    *,
    presets: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Read a preset file into the library; returns the names that were added."""
    file = Path(source)
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CurveError(f"Could not read {file.name}: {exc}") from exc

    library = load() if presets is None else presets
    added: list[str] = []
    for name, spec in _importable(raw, file.stem):
        try:
            entry = _entry(spec, name)
        except CurveError as exc:
            log.warning("skipping %r from %s: %s", name, file, exc)
            continue
        final = unique_name(clean_name(name), library)
        library[final] = entry
        added.append(final)
    if not added:
        raise CurveError("no valid fan curves in this file")
    save(library)
    return added


def summary(curves: dict[str, dict[str, Any]]) -> str:
    """Short human label such as ``58-98 °C, 2 fans``."""
    spans = []
    for fan in FANS:
        curve = curves.get(fan)
        if not curve:
            continue
        temps = curve.get("temp") or []
        if temps:
            spans.append(f"{fan} {min(temps)}\u2013{max(temps)}\u00b0C")
    return " \u00b7 ".join(spans) if spans else "no curves"
