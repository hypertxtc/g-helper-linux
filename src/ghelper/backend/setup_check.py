# SPDX-License-Identifier: GPL-3.0-or-later

"""System readiness checks, privileged one-click fixes, and diagnostics report."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping

from .. import APP_VERSION
from . import anime as anime_mod
from . import asusd as asusd_mod
from . import cardwire as cw
from . import display, hotkeys, nvml, ppd
from . import slash as slash_mod
from .system import (
    HELPER_DIRS,
    NVIDIA_VENDOR,
    PCI_DEVICES,
    Monitor,
    _int,
    _text,
    machine_name,
    nvidia_pci_devices,
    rescan_helper,
)

log = logging.getLogger(__name__)

LSM_PATH = "/sys/kernel/security/lsm"
GRUB_BPF_CFG = "/etc/default/grub.d/90-ghelper-cardwire-lsm.cfg"
GRUB_D_DIR = "/etc/default/grub.d"
SETUP_FIX_HELPER_NAME = "setup-fix"
ALLOWED_FIXES = frozenset(
    {"start-asusd", "enable-cardwired", "reload-udev", "enable-bpf-lsm"}
)
_UNSET = object()


@dataclass(frozen=True)
class CheckItem:
    id: str
    title: str
    status: str  # "ok" | "warn" | "error" | "info"
    summary: str
    detail: str = ""
    fix_action: str | None = None
    fix_label: str | None = None
    required: bool = True


def read_active_lsms(path: str = LSM_PATH) -> list[str]:
    """Return active Linux Security Modules from ``/sys/kernel/security/lsm``."""
    raw = _text(path)
    if not raw:
        return []
    tokens: list[str] = []
    for part in raw.replace(",", " ").split():
        token = part.strip().lower()
        if token:
            tokens.append(token)
    return tokens


def grub_bpf_configured(path: str = GRUB_BPF_CFG) -> bool:
    """Whether g-helper-linux already wrote the GRUB BPF LSM drop-in."""
    return os.path.isfile(path)


def can_configure_grub_bpf(
    grub_d: str = GRUB_D_DIR, update_grub: str | None = None
) -> bool:
    """Whether ``/etc/default/grub.d`` and ``update-grub`` are available."""
    if not os.path.isdir(grub_d):
        return False
    cmd = "update-grub" if update_grub is None else update_grub
    return bool(cmd) and shutil.which(cmd) is not None


def detect_session(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return ``(session_type, desktop)`` from environment variables."""
    source = os.environ if env is None else env
    raw_type = (source.get("XDG_SESSION_TYPE") or "").strip().lower()
    if raw_type in ("wayland", "x11"):
        session_type = raw_type
    elif source.get("WAYLAND_DISPLAY"):
        session_type = "wayland"
    elif source.get("DISPLAY"):
        session_type = "x11"
    else:
        session_type = "unknown"
    desktop = (
        source.get("XDG_CURRENT_DESKTOP") or source.get("DESKTOP_SESSION") or ""
    ).strip()
    return session_type, desktop


def probe_nvidia_gpus(
    pci_root: str | None = None, nvml_ok: bool | None = None
) -> dict[str, Any] | None:
    """Inspect any NVIDIA display controller on PCI regardless of bound driver."""
    root = PCI_DEVICES if pci_root is None else pci_root
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return None

    devices: list[dict[str, Any]] = []
    for entry in entries:
        dev_path = os.path.join(root, entry)
        class_raw = _text(os.path.join(dev_path, "class"))
        if not class_raw:
            continue
        try:
            if int(class_raw, 16) >> 16 != 0x03:
                continue
        except ValueError:
            continue
        vendor = (_text(os.path.join(dev_path, "vendor")) or "").lower()
        if vendor != NVIDIA_VENDOR:
            continue
        driver_link = os.path.join(dev_path, "driver")
        driver = (
            os.path.basename(os.path.realpath(driver_link))
            if os.path.islink(driver_link)
            else None
        )
        status = _text(os.path.join(dev_path, "power", "runtime_status"))
        control = _text(os.path.join(dev_path, "power", "control"))
        devices.append(
            {
                "pci": entry,
                "driver": driver,
                "runtime_status": status,
                "power_control": control,
                "boot_vga": _int(os.path.join(dev_path, "boot_vga")),
            }
        )

    if not devices:
        return None

    drivers = [dev["driver"] for dev in devices if dev["driver"]]
    if "nvidia" in drivers:
        primary_driver: str | None = "nvidia"
    elif "nouveau" in drivers:
        primary_driver = "nouveau"
    else:
        primary_driver = drivers[0] if drivers else None

    nvml_ready = nvml.available() if nvml_ok is None else bool(nvml_ok)
    runtime_statuses = [
        dev["runtime_status"] for dev in devices if dev.get("runtime_status")
    ]
    runtime_pm_auto = any(
        dev.get("power_control") == "auto" or dev.get("runtime_status") == "suspended"
        for dev in devices
    )
    return {
        "devices": devices,
        "driver": primary_driver,
        "nvml_available": nvml_ready,
        "runtime_pm_auto": runtime_pm_auto,
        "runtime_statuses": runtime_statuses,
    }


def setup_fix_helper() -> Path | None:
    """Locate the installed ``setup-fix`` helper (or checkout fallback)."""
    override = os.environ.get("GHELPER_LIBEXECDIR")
    if override:
        candidate = Path(override) / SETUP_FIX_HELPER_NAME
        if candidate.is_file():
            return candidate
    for directory in HELPER_DIRS:
        candidate = directory / SETUP_FIX_HELPER_NAME
        if candidate.is_file():
            return candidate
    checkout_helper = (
        Path(__file__).resolve().parents[3] / "data" / "ghelper-setup-fix"
    )
    if checkout_helper.is_file():
        return checkout_helper
    return None


def run_fix(action: str, timeout: float = 180.0) -> None:
    """Execute one allowlisted prerequisite repair step via ``pkexec``."""
    if action not in ALLOWED_FIXES:
        raise ValueError(f"Unsupported setup fix action: {action!r}")
    pkexec = shutil.which("pkexec")
    if pkexec is None:
        raise OSError(
            "pkexec is not installed; run the configuration command with sudo instead."
        )
    helper = setup_fix_helper()
    if helper is None:
        raise OSError("ghelper-setup-fix helper could not be found.")
    try:
        proc = subprocess.run(
            [pkexec, str(helper), action],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("Authentication timed out") from exc
    except OSError as exc:
        raise OSError(str(exc)) from exc
    if proc.returncode == 0:
        return
    if proc.returncode in (126, 127):
        raise OSError("Authentication cancelled")
    message = (proc.stderr or proc.stdout or "").strip().splitlines()
    raise OSError(message[-1] if message else f"Setup step '{action}' failed")


def _asusd_installed() -> bool:
    return shutil.which("asusd") is not None or os.path.isfile("/usr/bin/asusd")


def _check_asusd(
    asusd_client: Any = None, asusd_installed: bool | None = None
) -> CheckItem:
    client = asusd_client
    err = ""
    connected = False
    try:
        if client is None:
            client = asusd_mod.Asusd()
        client.connect()
        connected = True
    except Exception as exc:  # noqa: BLE001
        err = str(exc)

    if connected:
        return CheckItem(
            id="asusd",
            title="ASUS Control Daemon (asusd)",
            status="ok",
            summary=f"Running (v{client.version})",
            required=True,
        )

    lowered = err.lower()
    if any(
        token in lowered
        for token in ("accessdenied", "permission", "not allowed", "rejected")
    ):
        return CheckItem(
            id="asusd",
            title="ASUS Control Daemon (asusd)",
            status="error",
            summary="D-Bus access denied",
            detail=(
                "Your account is not permitted to talk to asusd. "
                "Add your user to the 'sudo', 'adm', or 'wheel' group, then log out and back in."
            ),
            required=True,
        )

    installed = _asusd_installed() if asusd_installed is None else asusd_installed
    if installed:
        return CheckItem(
            id="asusd",
            title="ASUS Control Daemon (asusd)",
            status="error",
            summary="Service is not running",
            detail=f"asusd is installed, but not answering on D-Bus ({err or 'inactive'}).",
            fix_action="start-asusd",
            fix_label="Start asusd",
            required=True,
        )

    return CheckItem(
        id="asusd",
        title="ASUS Control Daemon (asusd)",
        status="error",
        summary="asusctl / asusd is not installed",
        detail=(
            "Run setup.sh (or install the asusctl package) for performance profiles, "
            "fan curves, lighting, and battery limits."
        ),
        required=True,
    )


def _check_bpf_lsm(
    lsms: list[str] | None,
    grub_configured: bool | None,
    grub_configurable: bool | None,
) -> tuple[CheckItem, bool]:
    active = read_active_lsms() if lsms is None else lsms
    bpf_active = "bpf" in active
    if bpf_active:
        return (
            CheckItem(
                id="bpf_lsm",
                title="Kernel BPF LSM (for GPU blocking)",
                status="ok",
                summary=f"Active ({', '.join(active)})",
                required=True,
            ),
            True,
        )

    if grub_bpf_configured() if grub_configured is None else grub_configured:
        return (
            CheckItem(
                id="bpf_lsm",
                title="Kernel BPF LSM (for GPU blocking)",
                status="warn",
                summary="Configured — reboot required",
                detail=(
                    "GRUB already includes 'bpf' for the next boot. "
                    "Reboot once to activate Cardwire GPU blocking."
                ),
                required=True,
            ),
            False,
        )

    current_list = ",".join(active) if active else "lockdown,capability,yama,apparmor"
    if can_configure_grub_bpf() if grub_configurable is None else grub_configurable:
        return (
            CheckItem(
                id="bpf_lsm",
                title="Kernel BPF LSM (for GPU blocking)",
                status="error",
                summary="Disabled in running kernel",
                detail=(
                    f"Cardwire requires 'bpf' alongside your security modules ({current_list}). "
                    "Click Enable BPF LSM to update GRUB safely, then reboot once."
                ),
                fix_action="enable-bpf-lsm",
                fix_label="Enable BPF LSM",
                required=True,
            ),
            False,
        )

    return (
        CheckItem(
            id="bpf_lsm",
            title="Kernel BPF LSM (for GPU blocking)",
            status="error",
            summary="Disabled in running kernel",
            detail=f"Add lsm={current_list},bpf to your bootloader kernel parameters and reboot.",
            required=True,
        ),
        False,
    )


def _check_cardwire(
    bpf_active: bool,
    cardwire_installed: bool | None,
    cardwire_alive: bool | None,
    cardwire_state: dict[str, Any] | None,
) -> CheckItem:
    installed = cw.available() if cardwire_installed is None else cardwire_installed
    if not installed:
        return CheckItem(
            id="cardwire",
            title="GPU Mode Daemon (cardwired)",
            status="error",
            summary="cardwire is not installed",
            detail="Run setup.sh or install cardwire for Eco, Standard, Smart, and Optimized modes.",
            required=True,
        )

    alive = cw.daemon_alive() if cardwire_alive is None else cardwire_alive
    if alive:
        state = cardwire_state
        if state is None:
            try:
                state = cw.get_state()
            except cw.CardwireError:
                state = {}
        mode = (state or {}).get("current") or "running"
        return CheckItem(
            id="cardwire",
            title="GPU Mode Daemon (cardwired)",
            status="ok",
            summary=f"Running (mode: {mode})",
            required=True,
        )

    if not bpf_active:
        return CheckItem(
            id="cardwire",
            title="GPU Mode Daemon (cardwired)",
            status="warn",
            summary="Waiting for Kernel BPF LSM",
            detail=(
                "cardwired cannot start until 'bpf' is active in the kernel LSM list. "
                "Enable the service now so it starts automatically after reboot."
            ),
            fix_action="enable-cardwired",
            fix_label="Enable cardwired",
            required=True,
        )

    return CheckItem(
        id="cardwire",
        title="GPU Mode Daemon (cardwired)",
        status="error",
        summary="Service is not running",
        detail="cardwire is installed, but cardwired.service is not enabled or started.",
        fix_action="enable-cardwired",
        fix_label="Enable & Start",
        required=True,
    )


def _check_session(session: tuple[str, str] | None) -> CheckItem:
    session_type, desktop = detect_session() if session is None else session
    desktop_suffix = f" ({desktop})" if desktop else ""
    if session_type == "wayland":
        return CheckItem(
            id="session",
            title="Display Session",
            status="ok",
            summary=f"Wayland{desktop_suffix}",
            required=True,
        )
    if session_type == "x11":
        return CheckItem(
            id="session",
            title="Display Session",
            status="warn",
            summary=f"X11 session detected{desktop_suffix} — Cardwire requires Wayland",
            detail=(
                "Switch at the login screen: Log out → click your username → "
                "click the bottom-right gear icon → choose Zorin Desktop on Wayland "
                "(or GNOME/Plasma Wayland)."
            ),
            required=True,
        )
    return CheckItem(
        id="session",
        title="Display Session",
        status="info",
        summary="Session type unknown",
        detail="Cardwire GPU blocking requires a Wayland desktop session.",
        required=False,
    )


def _check_hotkeys(hotkeys_report: dict[str, Any] | None) -> CheckItem:
    report = hotkeys.probe() if hotkeys_report is None else hotkeys_report
    devices = report.get("devices") or []
    if report.get("readable"):
        names = (
            ", ".join(
                dict.fromkeys(d["name"] for d in devices if d.get("readable") and d.get("name"))
            )
            or "input device"
        )
        return CheckItem(
            id="hotkeys",
            title="Fn / ROG Hotkeys",
            status="ok",
            summary=f"Readable ({names})",
            required=False,
        )
    if devices:
        return CheckItem(
            id="hotkeys",
            title="Fn / ROG Hotkeys",
            status="warn",
            summary="Input devices found, permission needed",
            detail="Reload the udev rules to enable Fn / ROG key reading without rebooting.",
            fix_action="reload-udev",
            fix_label="Reload rules",
            required=False,
        )
    return CheckItem(
        id="hotkeys",
        title="Fn / ROG Hotkeys",
        status="info",
        summary="No ASUS WMI / N-KEY hotkey devices detected",
        detail="Normal keyboard shortcuts still work; some models do not expose extra WMI keys.",
        required=False,
    )


def _check_nvidia(nvidia_info: Any) -> CheckItem | None:
    info = probe_nvidia_gpus() if nvidia_info is _UNSET else nvidia_info
    if info is None:
        return None
    driver = info.get("driver")
    nvml_ok = bool(info.get("nvml_available"))
    pm_auto = bool(info.get("runtime_pm_auto"))
    pm_note = "runtime PM auto" if pm_auto else "runtime PM not auto"
    if driver == "nvidia" and nvml_ok:
        return CheckItem(
            id="nvidia",
            title="NVIDIA Discrete GPU",
            status="ok",
            summary=f"Proprietary driver active, NVML ready ({pm_note})",
            required=False,
        )
    if driver == "nvidia":
        return CheckItem(
            id="nvidia",
            title="NVIDIA Discrete GPU",
            status="warn",
            summary="Proprietary driver active, NVML library missing",
            detail="Install libnvidia-ml (NVIDIA driver utilities) for live GPU temperature and wattage.",
            required=False,
        )
    if driver == "nouveau":
        return CheckItem(
            id="nvidia",
            title="NVIDIA Discrete GPU",
            status="warn",
            summary="Using open-source nouveau driver",
            detail=(
                "For best gaming performance and NVML telemetry on Zorin OS / Ubuntu, "
                "install the proprietary NVIDIA driver in Software & Updates → Additional Drivers."
            ),
            required=False,
        )
    return CheckItem(
        id="nvidia",
        title="NVIDIA Discrete GPU",
        status="warn",
        summary="NVIDIA GPU detected without an active graphics driver",
        detail="Install the proprietary NVIDIA driver (Additional Drivers on Zorin/Ubuntu).",
        required=False,
    )


def collect_checks(
    *,
    asusd_client: Any = None,
    asusd_installed: bool | None = None,
    lsms: list[str] | None = None,
    grub_configured: bool | None = None,
    grub_configurable: bool | None = None,
    cardwire_installed: bool | None = None,
    cardwire_alive: bool | None = None,
    cardwire_state: dict[str, Any] | None = None,
    session: tuple[str, str] | None = None,
    hotkeys_report: dict[str, Any] | None = None,
    nvidia_info: Any = _UNSET,
) -> list[CheckItem]:
    """Run all prerequisite checks and return structured status items."""
    asusd_item = _check_asusd(asusd_client, asusd_installed)
    bpf_item, bpf_active = _check_bpf_lsm(lsms, grub_configured, grub_configurable)
    cw_item = _check_cardwire(
        bpf_active, cardwire_installed, cardwire_alive, cardwire_state
    )
    session_item = _check_session(session)
    hotkey_item = _check_hotkeys(hotkeys_report)
    items = [asusd_item, bpf_item, cw_item, session_item, hotkey_item]
    nvidia_item = _check_nvidia(nvidia_info)
    if nvidia_item is not None:
        items.append(nvidia_item)
    return items


def needs_attention(items: list[CheckItem]) -> bool:
    """Whether onboarding should pop up automatically for ``items``."""
    return any(
        item.status == "error" or (item.required and item.status == "warn")
        for item in items
    )


def summary_state(items: list[CheckItem]) -> tuple[str, str, str]:
    """Return ``(status_key, headline, subtitle)`` for the Setup Check header."""
    if any(item.status == "error" for item in items):
        return (
            "error",
            "Required setup steps need attention",
            "Run any available fix below, then click Re-check.",
        )
    if any(item.required and item.status == "warn" for item in items):
        return (
            "warn",
            "Almost ready — one step needs completion",
            "Review the highlighted item below (a reboot or Wayland login may be required).",
        )
    if any(item.status == "warn" for item in items):
        return (
            "warn",
            "Ready with optional recommendations",
            "Core controls are healthy; optional hardware notes are listed below.",
        )
    return (
        "ok",
        "Everything is ready",
        "All required ASUS services, GPU controls, and permissions look healthy.",
    )


def build_diagnostics_report() -> dict[str, Any]:
    """Build the complete backend and setup diagnostics JSON payload."""
    report: dict[str, Any] = {"machine": machine_name(), "version": APP_VERSION}
    helper = rescan_helper()
    setup_helper = setup_fix_helper()
    report["rescan"] = {
        "helper": str(helper) if helper is not None else None,
        "setup_helper": str(setup_helper) if setup_helper is not None else None,
        "libexec_override": os.environ.get("GHELPER_LIBEXECDIR") or None,
    }
    try:
        client = asusd_mod.Asusd()
        client.connect()
        report["asusd"] = {
            "available": True,
            "version": client.version,
            "profile": client.platform_profile(),
            "profile_choices": client.platform_choices(),
            "charge_limit": client.charge_limit(),
            "fan_curves_balanced": client.fan_curves(asusd_mod.BALANCED),
            "armoury": {
                name: client.armoury(name)
                for name in (
                    asusd_mod.ATTR_DGPU_DISABLE,
                    asusd_mod.ATTR_GPU_MUX,
                    asusd_mod.ATTR_PANEL_OVERDRIVE,
                    asusd_mod.ATTR_PPT_APU,
                    asusd_mod.ATTR_PPT_PLATFORM,
                    asusd_mod.ATTR_PPT_FPPT,
                )
                if client.armoury_available(name)
            },
            "resolved_attributes": client.resolved_attributes(),
        }
    except Exception as exc:  # noqa: BLE001
        report["asusd"] = {"available": False, "error": str(exc)}

    try:
        report["cardwire"] = {"available": cw.available(), **cw.get_state()}
        report["cardwire"]["gpus"] = cw.list_gpus()
    except Exception as exc:  # noqa: BLE001
        report["cardwire"] = {"available": cw.available(), "error": str(exc)}

    report["display"] = {
        "available": display.available(),
        "backend": display.backend_name(),
    }
    if report["display"]["available"]:
        try:
            panel = display.primary_panel()
            report["display"]["panel"] = panel["name"] if panel else None
            report["display"]["refresh_rates"] = (
                display.refresh_rates(panel) if panel else []
            )
        except Exception as exc:  # noqa: BLE001
            report["display"]["error"] = str(exc)
    else:
        report["display"]["reason"] = display.unavailable_reason()

    report["nvidia"] = {
        "nvml_available": nvml.available(),
        "devices": nvidia_pci_devices(),
        "probe": probe_nvidia_gpus(),
    }

    slash = slash_mod.Slash()
    try:
        report["slash"] = (
            {"available": True, **slash.state()}
            if slash.available
            else {"available": False}
        )
    except Exception as exc:  # noqa: BLE001
        report["slash"] = {"available": False, "error": str(exc)}

    anime = anime_mod.Anime()
    try:
        report["anime"] = (
            {"available": True, **anime.state()}
            if anime.available
            else {"available": False}
        )
    except Exception as exc:  # noqa: BLE001
        report["anime"] = {"available": False, "error": str(exc)}

    hotkeys_report = hotkeys.probe()
    sess_type, desktop = detect_session()
    lsms = read_active_lsms()
    report["sensors"] = Monitor().snapshot()
    report["hotkeys"] = hotkeys_report
    report["power_profiles"] = ppd.state()
    report["session"] = {"type": sess_type, "desktop": desktop or None}
    report["lsm"] = {"active": lsms, "bpf_enabled": "bpf" in lsms}
    report["setup"] = [
        asdict(item)
        for item in collect_checks(
            lsms=lsms,
            session=(sess_type, desktop),
            hotkeys_report=hotkeys_report,
            nvidia_info=report["nvidia"]["probe"],
        )
    ]
    return report
