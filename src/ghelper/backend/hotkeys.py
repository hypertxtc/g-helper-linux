# SPDX-License-Identifier: GPL-3.0-or-later

"""Passive reader for ASUS hotkey devices (Fn keys, ROG/Armoury key).

Events are read but never grabbed, so every key keeps whatever function the
firmware and desktop already give it.  Reading needs access to
``/dev/input/event*``; the udev rule installed with ``make install`` grants the
logged-in user read access through ``uaccess``.
"""

from __future__ import annotations

import logging
import os
import stat
import struct
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

from ..deps import GLib

log = logging.getLogger(__name__)

EVENT = struct.Struct("llHHi")
EVENT_SIZE = EVENT.size
EV_KEY = 0x01
KEY_PRESS = 1

PROC_DEVICES = "/proc/bus/input/devices"
DEVICE_ENV = "GHELPER_HOTKEY_DEVICE"

#: Names the kernel gives the useful devices on ASUS laptops.
WANTED_NAMES = ("Asus WMI hotkeys", "N-KEY Device", "Asus Keyboard", "ROG")

KEY_NAMES = {
    50: "KEY_M",
    51: "KEY_COMMA",
    86: "KEY_102ND",
    113: "KEY_MUTE",
    114: "KEY_VOLUMEDOWN",
    115: "KEY_VOLUMEUP",
    142: "KEY_SLEEP",
    148: "KEY_PROG1",
    149: "KEY_PROG2",
    152: "KEY_COFFEE",
    163: "KEY_NEXTSONG",
    164: "KEY_PLAYPAUSE",
    165: "KEY_PREVIOUSSONG",
    166: "KEY_STOPCD",
    202: "KEY_PROG3",
    203: "KEY_PROG4",
    212: "KEY_CAMERA",
    215: "KEY_EMAIL",
    224: "KEY_BRIGHTNESSDOWN",
    225: "KEY_BRIGHTNESSUP",
    227: "KEY_SWITCHVIDEOMODE",
    228: "KEY_KBDILLUMTOGGLE",
    229: "KEY_KBDILLUMDOWN",
    230: "KEY_KBDILLUMUP",
    236: "KEY_BATTERY",
    238: "KEY_WLAN",
    240: "KEY_UNKNOWN",
    244: "KEY_BRIGHTNESS_AUTO",
    245: "KEY_DISPLAY_OFF",
    246: "KEY_WWAN",
    248: "KEY_MICMUTE",
    465: "KEY_FN_ESC",
}

KEY_LABELS = {
    202: "ROG (Armoury Crate) key",
    203: "Aura (keyboard) key",
    212: "Camera key",
    215: "Mail key",
    227: "Display switch",
    228: "Keyboard light toggle",
    236: "Battery key",
    238: "Wireless key",
    244: "Ambient light key",
    245: "Display off",
    246: "Cellular key",
    248: "Microphone mute",
}

ACTIONS = {
    "toggle_window": "Open / hide G-Helper",
    "show_window": "Open G-Helper",
    "profile_next": "Next performance mode",
    "profile_quiet": "Silent mode",
    "profile_balanced": "Balanced mode",
    "profile_performance": "Turbo mode",
    "keyboard_up": "Keyboard brighter",
    "keyboard_down": "Keyboard dimmer",
    "keyboard_toggle": "Keyboard light on / off",
    "ignore": "Ignore",
}

ACTION_ORDER = tuple(ACTIONS)

#: KEY_PROG3 is the Armoury Crate / ROG key on ASUS laptops.
DEFAULT_FN_ACTIONS = {"202": "toggle_window"}

HINT = (
    "Fn/hotkey devices are not readable. Install the udev rule with "
    "`sudo make install`, then run `sudo udevadm control --reload-rules && "
    "sudo udevadm trigger --subsystem-match=input` (or simply reboot)."
)


class HotkeyError(RuntimeError):
    """Raised when no hotkey device can be opened."""


@dataclass(frozen=True)
class HotkeyDevice:
    name: str
    path: str
    handlers: tuple[str, ...] = ()


@dataclass(frozen=True)
class HotkeyEvent:
    code: int
    device: str


def parse_devices(text: str) -> list[HotkeyDevice]:
    """Parse ``/proc/bus/input/devices`` blocks into device records."""
    devices: list[HotkeyDevice] = []
    for block in text.split("\n\n"):
        name = None
        handlers: tuple[str, ...] = ()
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[len("N: Name=") :].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = tuple(line[len("H: Handlers=") :].split())
        if not name or not handlers:
            continue
        events = [entry for entry in handlers if entry.startswith("event")]
        if not events:
            continue
        devices.append(
            HotkeyDevice(
                name=name,
                path=f"/dev/input/{events[0]}",
                handlers=handlers,
            )
        )
    return devices


def is_hotkey_device(device: HotkeyDevice) -> bool:
    if "kbd" not in device.handlers:
        return False
    lowered = device.name.lower()
    return any(part.lower() in lowered for part in WANTED_NAMES)


def read_proc_devices() -> str:
    try:
        with open(PROC_DEVICES, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        log.debug("could not read %s: %s", PROC_DEVICES, exc)
        return ""


def discover(extra: Iterable[str] = ()) -> list[HotkeyDevice]:
    devices = [d for d in parse_devices(read_proc_devices()) if is_hotkey_device(d)]
    paths = set(extra)
    paths.update(
        entry for entry in os.environ.get(DEVICE_ENV, "").split(":") if entry
    )
    for path in sorted(paths):
        if os.path.exists(path):
            devices.append(HotkeyDevice(name="test device", path=path))
    return devices


def decode_event(raw: bytes) -> tuple[int, int, int]:
    _seconds, _micros, type_, code, value = EVENT.unpack(raw)
    return type_, code, value


def key_name(code: int) -> str:
    return KEY_NAMES.get(code, f"KEY_0x{code:x}")


def key_label(code: int) -> str:
    return KEY_LABELS.get(code) or key_name(code)


class HotkeyListener:
    """GLib-integrated, passive evdev reader."""

    def __init__(self, on_key: Callable[[HotkeyEvent], None]) -> None:
        self._on_key = on_key
        self._sources: list[int] = []
        self._devices: dict[int, HotkeyDevice] = {}
        self._buffers: dict[int, bytes] = {}
        self._opened: list[HotkeyDevice] = []
        self._errors: list[str] = []

    @property
    def devices(self) -> list[HotkeyDevice]:
        return list(self._opened)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def start(self) -> list[HotkeyDevice]:
        if self._sources:
            return self.devices
        candidates = discover()

        for device in candidates:
            try:
                flags = os.O_RDONLY | os.O_NONBLOCK
                try:
                    if stat.S_ISFIFO(os.stat(device.path).st_mode):
                        # Test hook: keep a writer so reads do not hit EOF.
                        flags = os.O_RDWR | os.O_NONBLOCK
                except OSError:
                    pass
                fd = os.open(device.path, flags)
            except OSError as exc:
                self._errors.append(f"{device.path}: {exc.strerror or exc}")
                continue
            self._devices[fd] = device
            self._buffers[fd] = b""
            self._opened.append(device)
            self._sources.append(
                GLib.io_add_watch(
                    fd,
                    GLib.PRIORITY_DEFAULT,
                    GLib.IOCondition.IN,
                    self._on_readable,
                    device,
                )
            )

        if not self._opened:
            raise HotkeyError(HINT)
        log.info(
            "reading hotkeys from %s",
            ", ".join(f"{d.name} ({d.path})" for d in self._opened),
        )
        return self.devices

    def stop(self) -> None:
        for source in self._sources:
            GLib.source_remove(source)
        self._sources.clear()
        for fd in self._devices:
            try:
                os.close(fd)
            except OSError:
                pass
        self._devices.clear()
        self._buffers.clear()
        self._opened.clear()

    def _on_readable(self, fd: int, _condition, device: HotkeyDevice) -> bool:
        try:
            chunk = os.read(fd, EVENT_SIZE * 64)
        except BlockingIOError:
            return True
        except OSError as exc:
            log.debug("hotkey read failed on %s: %s", device.path, exc)
            return False
        if not chunk:
            return True

        buffer = self._buffers.get(fd, b"") + chunk
        while len(buffer) >= EVENT_SIZE:
            raw, buffer = buffer[:EVENT_SIZE], buffer[EVENT_SIZE:]
            type_, code, value = decode_event(raw)
            if type_ == EV_KEY and value == KEY_PRESS:
                try:
                    self._on_key(HotkeyEvent(code=code, device=device.name))
                except Exception:  # noqa: BLE001 - never kill the reader
                    log.exception("hotkey handler failed")
        self._buffers[fd] = buffer
        return True


def probe() -> dict:
    """Report which hotkey devices exist and whether this user can read them."""
    devices = discover()
    result = []
    for device in devices:
        readable = False
        try:
            fd = os.open(device.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            readable = False
        else:
            os.close(fd)
            readable = True
        result.append(
            {"name": device.name, "path": device.path, "readable": readable}
        )
    return {"devices": result, "readable": any(d["readable"] for d in result)}


def run_learn() -> int:
    """``--learn-fn``: print key codes until interrupted."""
    import signal

    print("ASUS hotkey reader (passive). Press keys on the laptop to see them.")
    for device in discover():
        print(f"  device: {device.name}  {device.path}")
    print("Press Ctrl+C to stop.\n")

    def on_key(event: HotkeyEvent) -> None:
        print(f"{key_name(event.code):<22} code={event.code:<4} [{event.device}]")

    listener = HotkeyListener(on_key)
    try:
        listener.start()
    except HotkeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    loop = GLib.MainLoop()
    previous = None

    def on_sigint(_signum, _frame):
        loop.quit()

    try:
        previous = signal.signal(signal.SIGINT, on_sigint)
    except ValueError:
        previous = None
    try:
        loop.run()
    finally:
        listener.stop()
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
    return 0
