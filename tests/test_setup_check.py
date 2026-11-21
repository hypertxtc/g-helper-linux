# SPDX-License-Identifier: GPL-3.0-or-later

"""Headless unit tests for prerequisite checks, NVIDIA probing, and setup fixes."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghelper.backend import setup_check  # noqa: E402


class _FakeAsusd:
    def __init__(self, *, version: str = "6.5.0", exc: Exception | None = None) -> None:
        self.version = version
        self._exc = exc

    def connect(self) -> None:
        if self._exc is not None:
            raise self._exc


def _make_pci_device(
    root: Path,
    bdf: str,
    *,
    vendor: str,
    pci_class: str = "0x030000",
    driver: str | None = None,
    runtime_status: str | None = None,
    power_control: str | None = None,
    boot_vga: str | None = None,
) -> Path:
    dev = root / bdf
    dev.mkdir(parents=True)
    (dev / "vendor").write_text(vendor + "\n", encoding="utf-8")
    (dev / "class").write_text(pci_class + "\n", encoding="utf-8")
    if boot_vga is not None:
        (dev / "boot_vga").write_text(boot_vga + "\n", encoding="utf-8")
    if driver is not None:
        drv_target = root / "_drivers" / driver
        drv_target.mkdir(parents=True, exist_ok=True)
        os.symlink(drv_target, dev / "driver")
    if runtime_status is not None or power_control is not None:
        pwr = dev / "power"
        pwr.mkdir()
        if runtime_status is not None:
            (pwr / "runtime_status").write_text(runtime_status + "\n", encoding="utf-8")
        if power_control is not None:
            (pwr / "control").write_text(power_control + "\n", encoding="utf-8")
    return dev


class LsmAndSessionTests(unittest.TestCase):
    def test_read_active_lsms_parses_list_or_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lsm_file = Path(tmp) / "lsm"
            lsm_file.write_text("lockdown,capability,yama,apparmor,bpf\n", encoding="utf-8")
            self.assertEqual(
                setup_check.read_active_lsms(str(lsm_file)),
                ["lockdown", "capability", "yama", "apparmor", "bpf"],
            )
            self.assertEqual(setup_check.read_active_lsms(str(Path(tmp) / "missing")), [])

    def test_grub_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = tmp_path / "90-ghelper-cardwire-lsm.cfg"
            self.assertFalse(setup_check.grub_bpf_configured(str(cfg)))
            cfg.write_text("lsm=apparmor,bpf\n", encoding="utf-8")
            self.assertTrue(setup_check.grub_bpf_configured(str(cfg)))

            grub_d = tmp_path / "grub.d"
            grub_d.mkdir()
            fake_bin = tmp_path / "update-grub"
            fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_bin.chmod(0o755)
            self.assertTrue(
                setup_check.can_configure_grub_bpf(str(grub_d), update_grub=str(fake_bin))
            )
            self.assertFalse(
                setup_check.can_configure_grub_bpf(str(grub_d), update_grub="")
            )
            self.assertFalse(
                setup_check.can_configure_grub_bpf(
                    str(tmp_path / "missing"), update_grub=str(fake_bin)
                )
            )

    def test_detect_session_variants(self) -> None:
        self.assertEqual(
            setup_check.detect_session(
                {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}
            ),
            ("wayland", "KDE"),
        )
        self.assertEqual(
            setup_check.detect_session(
                {"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "zorin:GNOME"}
            ),
            ("x11", "zorin:GNOME"),
        )
        self.assertEqual(
            setup_check.detect_session({"WAYLAND_DISPLAY": "wayland-0"}),
            ("wayland", ""),
        )
        self.assertEqual(
            setup_check.detect_session({"DISPLAY": ":0"}),
            ("x11", ""),
        )
        self.assertEqual(setup_check.detect_session({}), ("unknown", ""))


class NvidiaProbeTests(unittest.TestCase):
    def test_amd_only_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_device(root, "0000:03:00.0", vendor="0x1002", driver="amdgpu")
            self.assertIsNone(setup_check.probe_nvidia_gpus(str(root), nvml_ok=False))

    def test_nvidia_proprietary_with_nvml_and_rtd3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_device(
                root,
                "0000:01:00.0",
                vendor="0x10de",
                driver="nvidia",
                runtime_status="suspended",
                power_control="auto",
                boot_vga="0",
            )
            info = setup_check.probe_nvidia_gpus(str(root), nvml_ok=True)
            assert info is not None
            self.assertEqual(info["driver"], "nvidia")
            self.assertTrue(info["nvml_available"])
            self.assertTrue(info["runtime_pm_auto"])
            self.assertEqual(info["runtime_statuses"], ["suspended"])

    def test_nvidia_proprietary_missing_nvml_and_nouveau(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_device(root, "0000:01:00.0", vendor="0x10de", driver="nvidia")
            info = setup_check.probe_nvidia_gpus(str(root), nvml_ok=False)
            assert info is not None
            self.assertEqual(info["driver"], "nvidia")
            self.assertFalse(info["nvml_available"])
            self.assertFalse(info["runtime_pm_auto"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_pci_device(root, "0000:01:00.0", vendor="0x10de", driver="nouveau")
            info = setup_check.probe_nvidia_gpus(str(root), nvml_ok=False)
            assert info is not None
            self.assertEqual(info["driver"], "nouveau")


class CollectChecksTests(unittest.TestCase):
    def test_healthy_wayland_amd_system(self) -> None:
        items = setup_check.collect_checks(
            asusd_client=_FakeAsusd(version="6.5.0"),
            lsms=["lockdown", "capability", "yama", "selinux", "bpf"],
            cardwire_installed=True,
            cardwire_alive=True,
            cardwire_state={"current": "hybrid"},
            session=("wayland", "KDE"),
            hotkeys_report={
                "devices": [{"name": "N-KEY Device", "readable": True}],
                "readable": True,
            },
            nvidia_info=None,
        )
        self.assertFalse(setup_check.needs_attention(items))
        self.assertEqual(setup_check.summary_state(items)[0], "ok")
        self.assertEqual([item.id for item in items], ["asusd", "bpf_lsm", "cardwire", "session", "hotkeys"])
        self.assertTrue(all(item.status == "ok" for item in items))

    def test_fresh_zorin_nvidia_x11_needs_fixes(self) -> None:
        items = setup_check.collect_checks(
            asusd_client=_FakeAsusd(exc=RuntimeError("asusd is not running")),
            asusd_installed=True,
            lsms=["lockdown", "capability", "yama", "apparmor"],
            grub_configured=False,
            grub_configurable=True,
            cardwire_installed=True,
            cardwire_alive=False,
            session=("x11", "zorin:GNOME"),
            hotkeys_report={
                "devices": [{"name": "Asus WMI hotkeys", "readable": False}],
                "readable": False,
            },
            nvidia_info={
                "devices": [{"pci": "0000:01:00.0"}],
                "driver": "nvidia",
                "nvml_available": True,
                "runtime_pm_auto": True,
            },
        )
        self.assertTrue(setup_check.needs_attention(items))
        by_id = {item.id: item for item in items}
        self.assertEqual(by_id["asusd"].status, "error")
        self.assertEqual(by_id["asusd"].fix_action, "start-asusd")
        self.assertEqual(by_id["bpf_lsm"].status, "error")
        self.assertEqual(by_id["bpf_lsm"].fix_action, "enable-bpf-lsm")
        self.assertEqual(by_id["cardwire"].status, "warn")
        self.assertEqual(by_id["cardwire"].fix_action, "enable-cardwired")
        self.assertEqual(by_id["session"].status, "warn")
        self.assertIn("Wayland", by_id["session"].detail)
        self.assertEqual(by_id["hotkeys"].status, "warn")
        self.assertEqual(by_id["hotkeys"].fix_action, "reload-udev")
        self.assertEqual(by_id["nvidia"].status, "ok")
        self.assertEqual(setup_check.summary_state(items)[0], "error")

    def test_grub_configured_awaits_reboot_without_fix_button(self) -> None:
        items = setup_check.collect_checks(
            asusd_client=_FakeAsusd(),
            lsms=["lockdown", "capability", "yama", "apparmor"],
            grub_configured=True,
            grub_configurable=True,
            cardwire_installed=True,
            cardwire_alive=False,
            session=("wayland", "GNOME"),
            hotkeys_report={"devices": [], "readable": False},
            nvidia_info=None,
        )
        by_id = {item.id: item for item in items}
        self.assertEqual(by_id["bpf_lsm"].status, "warn")
        self.assertIsNone(by_id["bpf_lsm"].fix_action)
        self.assertIn("reboot", by_id["bpf_lsm"].summary.lower())
        self.assertTrue(setup_check.needs_attention(items))
        self.assertEqual(setup_check.summary_state(items)[0], "warn")

    def test_missing_packages_offer_no_pkexec_fix(self) -> None:
        items = setup_check.collect_checks(
            asusd_client=_FakeAsusd(exc=RuntimeError("not running")),
            asusd_installed=False,
            lsms=["bpf"],
            cardwire_installed=False,
            session=("wayland", "KDE"),
            hotkeys_report={"devices": [], "readable": False},
            nvidia_info=None,
        )
        by_id = {item.id: item for item in items}
        self.assertEqual(by_id["asusd"].status, "error")
        self.assertIsNone(by_id["asusd"].fix_action)
        self.assertEqual(by_id["cardwire"].status, "error")
        self.assertIsNone(by_id["cardwire"].fix_action)

    def test_dbus_access_denied_reports_group_hint(self) -> None:
        items = setup_check.collect_checks(
            asusd_client=_FakeAsusd(
                exc=RuntimeError("org.freedesktop.DBus.Error.AccessDenied: rejected send message")
            ),
            asusd_installed=True,
            lsms=["bpf"],
            cardwire_installed=True,
            cardwire_alive=True,
            cardwire_state={"current": "hybrid"},
            session=("wayland", "KDE"),
            hotkeys_report={"devices": [], "readable": False},
            nvidia_info=None,
        )
        by_id = {item.id: item for item in items}
        self.assertEqual(by_id["asusd"].status, "error")
        self.assertIsNone(by_id["asusd"].fix_action)
        self.assertIn("group", by_id["asusd"].detail.lower())

    def test_optional_warning_summary_does_not_force_onboarding(self) -> None:
        items = setup_check.collect_checks(
            asusd_client=_FakeAsusd(),
            lsms=["bpf"],
            cardwire_installed=True,
            cardwire_alive=True,
            cardwire_state={"current": "hybrid"},
            session=("wayland", "GNOME"),
            hotkeys_report={"devices": [], "readable": False},
            nvidia_info={
                "devices": [{"pci": "0000:01:00.0"}],
                "driver": "nouveau",
                "nvml_available": False,
                "runtime_pm_auto": False,
            },
        )
        self.assertFalse(setup_check.needs_attention(items))
        state, headline, _sub = setup_check.summary_state(items)
        self.assertEqual(state, "warn")
        self.assertIn("optional", headline.lower())


class RunFixTests(unittest.TestCase):
    def test_rejects_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            setup_check.run_fix("rm -rf /")

    def test_missing_pkexec_raises(self) -> None:
        with mock.patch("ghelper.backend.setup_check.shutil.which", return_value=None):
            with self.assertRaisesRegex(OSError, "pkexec"):
                setup_check.run_fix("start-asusd")

    def test_cancelled_pkexec_raises_friendly_error(self) -> None:
        proc = subprocess.CompletedProcess(["pkexec"], 126, "", "dismissed")
        helper = Path("/usr/libexec/ghelper-linux/setup-fix")
        with (
            mock.patch(
                "ghelper.backend.setup_check.shutil.which", return_value="/usr/bin/pkexec"
            ),
            mock.patch(
                "ghelper.backend.setup_check.setup_fix_helper", return_value=helper
            ),
            mock.patch(
                "ghelper.backend.setup_check.subprocess.run", return_value=proc
            ),
        ):
            with self.assertRaisesRegex(OSError, "Authentication cancelled"):
                setup_check.run_fix("start-asusd")

    def test_failed_helper_reports_stderr(self) -> None:
        proc = subprocess.CompletedProcess(["pkexec"], 1, "", "line one\nGRUB missing\n")
        helper = Path("/usr/libexec/ghelper-linux/setup-fix")
        with (
            mock.patch(
                "ghelper.backend.setup_check.shutil.which", return_value="/usr/bin/pkexec"
            ),
            mock.patch(
                "ghelper.backend.setup_check.setup_fix_helper", return_value=helper
            ),
            mock.patch(
                "ghelper.backend.setup_check.subprocess.run", return_value=proc
            ),
        ):
            with self.assertRaisesRegex(OSError, "GRUB missing"):
                setup_check.run_fix("enable-bpf-lsm")

    def test_success_invokes_expected_command(self) -> None:
        proc = subprocess.CompletedProcess(["pkexec"], 0, "", "")
        helper = Path("/usr/libexec/ghelper-linux/setup-fix")
        with (
            mock.patch(
                "ghelper.backend.setup_check.shutil.which", return_value="/usr/bin/pkexec"
            ),
            mock.patch(
                "ghelper.backend.setup_check.setup_fix_helper", return_value=helper
            ),
            mock.patch(
                "ghelper.backend.setup_check.subprocess.run", return_value=proc
            ) as run_mock,
        ):
            setup_check.run_fix("enable-cardwired")
        run_mock.assert_called_once()
        self.assertEqual(
            run_mock.call_args.args[0],
            ["/usr/bin/pkexec", str(helper), "enable-cardwired"],
        )


class DiagnosticsAndOnboardingHookTests(unittest.TestCase):
    def test_build_diagnostics_report_includes_setup_fields(self) -> None:
        with (
            mock.patch("ghelper.backend.setup_check.asusd_mod.Asusd", return_value=_FakeAsusd(exc=RuntimeError("down"))),
            mock.patch("ghelper.backend.setup_check.cw.available", return_value=False),
            mock.patch("ghelper.backend.setup_check.display.available", return_value=False),
            mock.patch("ghelper.backend.setup_check.display.unavailable_reason", return_value="test"),
            mock.patch("ghelper.backend.setup_check.nvml.available", return_value=False),
            mock.patch("ghelper.backend.setup_check.nvidia_pci_devices", return_value=[]),
            mock.patch("ghelper.backend.setup_check.probe_nvidia_gpus", return_value=None),
            mock.patch("ghelper.backend.setup_check.Monitor.snapshot", return_value={}),
            mock.patch("ghelper.backend.setup_check.hotkeys.probe", return_value={"devices": [], "readable": False}),
            mock.patch("ghelper.backend.setup_check.ppd.state", return_value={"available": False}),
            mock.patch("ghelper.backend.setup_check.read_active_lsms", return_value=["apparmor", "bpf"]),
            mock.patch("ghelper.backend.setup_check.detect_session", return_value=("wayland", "GNOME")),
        ):
            report = setup_check.build_diagnostics_report()
        for key in (
            "machine",
            "version",
            "rescan",
            "asusd",
            "cardwire",
            "display",
            "nvidia",
            "slash",
            "anime",
            "sensors",
            "hotkeys",
            "power_profiles",
            "session",
            "lsm",
            "setup",
        ):
            with self.subTest(key=key):
                self.assertIn(key, report)
        self.assertEqual(report["session"], {"type": "wayland", "desktop": "GNOME"})
        self.assertTrue(report["lsm"]["bpf_enabled"])
        self.assertIsInstance(report["setup"], list)
        self.assertTrue(report["setup"])

    def test_maybe_open_onboarding_skips_when_already_seen(self) -> None:
        from ghelper.window import MainWindow

        fake_settings = mock.Mock()
        fake_settings.get.return_value = True
        win = MainWindow.__new__(MainWindow)
        win.settings = fake_settings
        win._onboarding_checked = False
        win.run = mock.Mock()
        win.maybe_open_onboarding()
        win.run.assert_not_called()

    def test_maybe_open_onboarding_marks_seen_when_healthy_or_opens_when_needed(self) -> None:
        from ghelper.window import MainWindow

        class _Store:
            def __init__(self) -> None:
                self.values = {"onboarding_seen": False}

            def get(self, key: str):
                return self.values.get(key)

            def set(self, key: str, value) -> None:
                self.values[key] = value

        # Healthy -> sets onboarding_seen=True silently.
        win = MainWindow.__new__(MainWindow)
        win.settings = _Store()
        win._onboarding_checked = False
        win.open_setup_window = mock.Mock()
        win.run = lambda fn, done=None, error=None: done(
            [setup_check.CheckItem(id="asusd", title="asusd", status="ok", summary="ok")]
        )
        win.maybe_open_onboarding()
        self.assertTrue(win.settings.get("onboarding_seen"))
        win.open_setup_window.assert_not_called()

        # Attention needed -> opens setup window with pre-collected items.
        bad_items = [
            setup_check.CheckItem(
                id="asusd", title="asusd", status="error", summary="stopped"
            )
        ]
        win2 = MainWindow.__new__(MainWindow)
        win2.settings = _Store()
        win2._onboarding_checked = False
        win2.open_setup_window = mock.Mock()
        win2.run = lambda fn, done=None, error=None: done(bad_items)
        win2.maybe_open_onboarding()
        self.assertFalse(win2.settings.get("onboarding_seen"))
        win2.open_setup_window.assert_called_once_with(initial_items=bad_items)


if __name__ == "__main__":
    unittest.main()
