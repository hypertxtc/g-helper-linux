# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure GPU-mode decisions, kept out of the GTK widget so they can be tested.

Two rules matter here and both are easy to break by accident:

* cardwire cannot change modes while firmware has the dGPU powered off, and
  the ``DgpuDisable`` attribute can read 0 while the card is still absent
  until the next boot or PCI rescan.  The PCI bus is the reliable signal.
* cardwire's CLI can block for seconds while the daemon struggles with a
  missing dGPU, so nothing on the UI thread may ask it whether its own
  AC/battery switch is enabled.

The view owns the I/O; these helpers own the decisions.
"""

from __future__ import annotations

from dataclasses import dataclass

# Outcomes reported by the Optimized button's worker.
OPTIMIZED_SWITCHED = "switched"
OPTIMIZED_FIRMWARE_ECO = "eco"
OPTIMIZED_DGPU_ABSENT = "absent"

# Outcomes reported by the mode buttons' worker.
MODE_OK = "ok"
MODE_CHANGED = "changed"
MODE_REBOOT = "reboot"


@dataclass(frozen=True)
class OptimizedPlan:
    """What the Optimized button should do after reading the live state."""

    action: str
    target: str | None = None

    @property
    def switched(self) -> bool:
        return self.target is not None


def optimized_target(ac: bool | None) -> str:
    """cardwire mode for Optimized: hybrid on AC, integrated on battery."""
    return "hybrid" if ac else "integrated"


def cardwire_can_switch(firmware_disabled: bool, dgpu_present: bool | None) -> bool:
    """Whether cardwire may be asked to change modes.

    ``dgpu_present`` is ``None`` when the bus could not be inspected; assume
    the card is there so a transient sysfs error does not silently disable
    every GPU mode.
    """
    return not firmware_disabled and dgpu_present is not False


def optimized_plan(
    firmware_disabled: bool, dgpu_present: bool | None, ac: bool | None
) -> OptimizedPlan:
    """Decide what Optimized can do in the current state."""
    if firmware_disabled:
        return OptimizedPlan(OPTIMIZED_FIRMWARE_ECO)
    if dgpu_present is False:
        return OptimizedPlan(OPTIMIZED_DGPU_ABSENT)
    return OptimizedPlan(OPTIMIZED_SWITCHED, optimized_target(ac))


def automation_should_skip(
    *,
    gpu_auto: bool,
    cw_available: bool,
    cw_auto: bool | None,
    firmware_disabled: bool,
    dgpu_present: bool | None,
) -> bool:
    """Whether AC/battery automation must leave the GPU alone.

    Defer to cardwire unless it is positively known to have its own switch
    off: the query is slow, and when the switch is on cardwire already moves
    between integrated and its configured mode on its own.
    """
    if not gpu_auto or not cw_available:
        return True
    if not cardwire_can_switch(firmware_disabled, dgpu_present):
        return True
    return cw_auto is not False


def firmware_queue_needed(cw_mode: str) -> bool:
    """Only Standard undoes firmware writes; Eco stays a runtime mode.

    Eco previously wrote the firmware ``dgpu_disable`` switch too, which
    powers the slot down and needs a rescan or reboot to come back.
    """
    return cw_mode == "hybrid"


def mode_outcome(
    *,
    changed: bool,
    firmware_disabled: bool,
    dgpu_present: bool | None,
    cw_mode: str,
) -> str:
    """Classify a mode button's work for the toast it should show."""
    if changed:
        return MODE_CHANGED
    if cw_mode == "hybrid" and not cardwire_can_switch(
        firmware_disabled, dgpu_present
    ):
        return MODE_REBOOT
    return MODE_OK


def cardwire_error_message(
    error: str, firmware_disabled: bool, dgpu_present: bool | None
) -> str | None:
    """Translate cardwire's raw ENOENT into something a user can act on.

    Returns ``None`` when the error should be shown unchanged.
    """
    if "No such file" not in error:
        return None
    if firmware_disabled:
        return (
            "The dGPU is disabled by the firmware ECO switch. "
            "Pick Standard to queue it back on, then reboot."
        )
    if dgpu_present is False:
        return "The dGPU is still off after ECO; reboot to bring it back."
    return None
