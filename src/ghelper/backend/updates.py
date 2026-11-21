# SPDX-License-Identifier: GPL-3.0-or-later

"""GitHub release update checker (standard-library HTTP, no GTK dependency)."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.request

from .. import APP_VERSION

RELEASE_API = "https://api.github.com/repos/hypertxtc/g-helper-linux/releases/latest"
RELEASE_PAGE = "https://github.com/hypertxtc/g-helper-linux/releases/latest"
UPDATE_COMMAND = (
    "curl -fsSL https://raw.githubusercontent.com/hypertxtc/g-helper-linux/main/setup.sh | bash"
)

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)")


class UpdateError(RuntimeError):
    """Raised when the latest release check cannot complete."""


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    available: bool
    url: str
    notes: str = ""
    assets: tuple[str, ...] = ()


def parse_version(text: str) -> tuple[int, ...] | None:
    """Parse a release tag or version string into an integer tuple."""
    match = _VERSION_RE.match((text or "").strip().lower())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    """Return True when ``latest`` represents a strictly higher version."""
    latest_parts = parse_version(latest)
    current_parts = parse_version(current)
    if latest_parts is None or current_parts is None:
        return False
    width = max(len(latest_parts), len(current_parts))
    padded_latest = latest_parts + (0,) * (width - len(latest_parts))
    padded_current = current_parts + (0,) * (width - len(current_parts))
    return padded_latest > padded_current


def summarize_notes(body: str, max_lines: int = 4) -> str:
    """Extract concise non-empty summary lines from release markdown."""
    lines: list[str] = []
    for raw in (body or "").splitlines():
        cleaned = raw.strip().lstrip("#").strip()
        if cleaned.startswith(("- ", "* ")):
            cleaned = cleaned[2:].strip()
        if not cleaned or cleaned.startswith("```"):
            continue
        lines.append(cleaned)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines)


def check_latest(
    current: str = APP_VERSION,
    *,
    api_url: str = RELEASE_API,
    timeout: float = 8.0,
) -> UpdateInfo:
    """Query GitHub Releases and compare against ``current``."""
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": f"g-helper-linux/{current}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
        raise UpdateError(f"Could not check for updates: {exc}") from exc

    if not isinstance(payload, dict):
        raise UpdateError("Unexpected response from update server")

    tag = str(payload.get("tag_name") or "").strip()
    latest = tag[1:] if tag.lower().startswith("v") else tag
    if not latest or parse_version(latest) is None:
        raise UpdateError("Latest release has no version tag")

    url = str(payload.get("html_url") or RELEASE_PAGE).strip() or RELEASE_PAGE
    notes = summarize_notes(str(payload.get("body") or ""))
    assets = tuple(
        str(asset["name"])
        for asset in (payload.get("assets") or [])
        if isinstance(asset, dict) and asset.get("name")
    )
    return UpdateInfo(
        current=current,
        latest=latest,
        available=is_newer(latest, current),
        url=url,
        notes=notes,
        assets=assets,
    )
