# SPDX-License-Identifier: GPL-3.0-or-later

"""Zero-dependency NVML telemetry reader via ctypes.

Only called when sysfs reports an NVIDIA dGPU with ``runtime_status`` active,
and shuts NVML down immediately after each sample so no ``/dev/nvidia*`` file
descriptor stays open to block runtime D3 power-down.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char_p, c_uint, c_void_p
import logging
from typing import Any

log = logging.getLogger(__name__)

NVML_SUCCESS = 0
NVML_TEMPERATURE_GPU = 0
LIB_NAMES = ("libnvidia-ml.so.1", "libnvidia-ml.so")

_MISSING = object()
_cached_lib: Any = None


def reset_cache() -> None:
    """Clear the cached NVML library handle (intended for unit tests)."""
    global _cached_lib
    _cached_lib = None


def _configure_lib(lib: Any) -> Any | None:
    init_fn = getattr(lib, "nvmlInit_v2", None) or getattr(lib, "nvmlInit", None)
    shutdown_fn = getattr(lib, "nvmlShutdown", None)
    handle_fn = getattr(lib, "nvmlDeviceGetHandleByPciBusId_v2", None) or getattr(
        lib, "nvmlDeviceGetHandleByPciBusId", None
    )
    temp_fn = getattr(lib, "nvmlDeviceGetTemperature", None)
    if not all((init_fn, shutdown_fn, handle_fn, temp_fn)):
        return None

    init_fn.argtypes = []
    init_fn.restype = c_uint
    shutdown_fn.argtypes = []
    shutdown_fn.restype = c_uint
    handle_fn.argtypes = [c_char_p, POINTER(c_void_p)]
    handle_fn.restype = c_uint
    temp_fn.argtypes = [c_void_p, c_uint, POINTER(c_uint)]
    temp_fn.restype = c_uint

    power_fn = getattr(lib, "nvmlDeviceGetPowerUsage", None)
    if power_fn is not None:
        power_fn.argtypes = [c_void_p, POINTER(c_uint)]
        power_fn.restype = c_uint
    return lib


def _load_lib() -> Any | None:
    global _cached_lib
    if _cached_lib is _MISSING:
        return None
    if _cached_lib is not None:
        return _cached_lib

    for name in LIB_NAMES:
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        configured = _configure_lib(lib)
        if configured is not None:
            _cached_lib = configured
            return _cached_lib
    _cached_lib = _MISSING
    return None


def available() -> bool:
    """Whether ``libnvidia-ml.so.1`` can be loaded with the expected symbols."""
    return _load_lib() is not None


def read_gpu(pci_address: str) -> dict[str, float | None] | None:
    """Return ``{"temp": °C | None, "power_w": W | None}`` for ``pci_address``."""
    lib = _load_lib()
    if lib is None or not pci_address:
        return None

    init_fn = getattr(lib, "nvmlInit_v2", None) or lib.nvmlInit
    if init_fn() != NVML_SUCCESS:
        return None

    try:
        handle = c_void_p()
        handle_fn = (
            getattr(lib, "nvmlDeviceGetHandleByPciBusId_v2", None)
            or lib.nvmlDeviceGetHandleByPciBusId
        )
        if handle_fn(pci_address.encode("ascii"), byref(handle)) != NVML_SUCCESS:
            return None

        temp = c_uint()
        temp_c = (
            float(temp.value)
            if lib.nvmlDeviceGetTemperature(handle, NVML_TEMPERATURE_GPU, byref(temp))
            == NVML_SUCCESS
            else None
        )

        power_w: float | None = None
        power_fn = getattr(lib, "nvmlDeviceGetPowerUsage", None)
        if power_fn is not None:
            power_mw = c_uint()
            if power_fn(handle, byref(power_mw)) == NVML_SUCCESS:
                power_w = power_mw.value / 1000.0

        return {"temp": temp_c, "power_w": power_w}
    finally:
        lib.nvmlShutdown()
