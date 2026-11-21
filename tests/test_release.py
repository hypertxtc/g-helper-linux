# SPDX-License-Identifier: GPL-3.0-or-later

"""Release metadata must agree with the code.

A version bump touches four files, and a mismatch is invisible until a package
is built or the AppStream data is rejected.  These checks make `make test` and
CI fail on drift instead.
"""

from __future__ import annotations

import datetime
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghelper import APP_VERSION  # noqa: E402

METAINFO = ROOT / "data/io.github.ghelperlinux.GHelper.metainfo.xml"


def metainfo_text() -> str:
    return METAINFO.read_text(encoding="utf-8")


def app_id() -> str:
    match = re.search(r"<id>([^<]+)</id>", metainfo_text())
    assert match is not None
    return match.group(1)


class VersionTests(unittest.TestCase):
    def test_pyproject_matches_app_version(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        assert match is not None
        self.assertEqual(match.group(1), APP_VERSION)

    def test_rpm_default_matches_app_version(self) -> None:
        text = (ROOT / "packaging/ghelper-linux.spec").read_text(encoding="utf-8")
        match = re.search(
            r"^Version:\s+%\{\?gh_version\}%\{!\?gh_version:([^}]+)\}",
            text,
            re.MULTILINE,
        )
        assert match is not None
        self.assertEqual(match.group(1), APP_VERSION)

    def test_appstream_release_matches_app_version(self) -> None:
        match = re.search(r'<release version="([^"]+)"', metainfo_text())
        assert match is not None
        self.assertEqual(match.group(1), APP_VERSION)

    def test_debian_changelog_matches_app_version(self) -> None:
        text = (ROOT / "debian/changelog").read_text(encoding="utf-8")
        match = re.match(r"ghelper-linux \(([^)]+)\)", text)
        assert match is not None
        self.assertEqual(match.group(1), f"{APP_VERSION}-1")

    def test_appstream_release_date_is_iso(self) -> None:
        match = re.search(r'<release version="[^"]+" date="([^"]+)"', metainfo_text())
        assert match is not None
        # Raises when the date is malformed.
        datetime.date.fromisoformat(match.group(1))

    def test_no_release_is_newer_than_app_version(self) -> None:
        versions = re.findall(r'<release version="([^"]+)"', metainfo_text())
        self.assertEqual(versions[0], APP_VERSION, "newest release must be first")


class IdentityTests(unittest.TestCase):
    def test_desktop_entry_is_named_after_the_app_id(self) -> None:
        self.assertTrue((ROOT / f"data/{app_id()}.desktop").is_file())

    def test_desktop_entry_matches_the_app_id(self) -> None:
        text = (ROOT / f"data/{app_id()}.desktop").read_text(encoding="utf-8")
        self.assertIn(f"Icon={app_id()}", text)
        self.assertIn("Exec=ghelper-linux", text)

    def test_desktop_entry_has_one_main_category(self) -> None:
        text = (ROOT / f"data/{app_id()}.desktop").read_text(encoding="utf-8")
        match = re.search(r"^Categories=(.+)$", text, re.MULTILINE)
        assert match is not None
        categories = [part for part in match.group(1).split(";") if part]
        self.assertEqual(categories[0], "Settings")
        self.assertNotIn("System", categories)

    def test_binary_is_advertised(self) -> None:
        match = re.search(r"<binary>([^<]+)</binary>", metainfo_text())
        assert match is not None
        self.assertEqual(match.group(1), "ghelper-linux")

    def test_launchable_points_at_the_desktop_entry(self) -> None:
        match = re.search(
            r'<launchable type="desktop-id">([^<]+)</launchable>', metainfo_text()
        )
        assert match is not None
        self.assertEqual(match.group(1), f"{app_id()}.desktop")


class InstalledFilesTests(unittest.TestCase):
    def test_shipped_files_exist(self) -> None:
        for relative in (
            "data/60-ghelper-hotkeys.rules",
            "data/ghelper-automation.service",
            "data/ghelper-rescan-pci",
            "docs/ghelper-linux.1",
            f"data/{app_id()}.desktop",
            f"data/{app_id()}.metainfo.xml",
            f"data/{app_id()}.policy",
        ):
            with self.subTest(path=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_man_page_is_troff(self) -> None:
        text = (ROOT / "docs/ghelper-linux.1").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(".TH GHELPER-LINUX 1"))


def _deb_field(control: str, field: str) -> set[str]:
    binary = control.split("\nPackage:", 1)[1]
    match = re.search(rf"^{field}:([^\n]*(?:\n[ \t]+[^\n]*)*)", binary, re.MULTILINE)
    assert match is not None, f"binary stanza missing {field}"
    items: set[str] = set()
    for part in match.group(1).split(","):
        token = part.strip().split()[0] if part.strip() else ""
        if token and not token.startswith("${"):
            items.add(token)
    return items


def _spec_deps(spec: str, tag: str) -> set[str]:
    return {match.group(1) for match in re.finditer(rf"^{tag}:\s*(\S+)", spec, re.MULTILINE)}


class DebianPackagingTests(unittest.TestCase):
    def test_debian_package_installs_through_the_makefile(self) -> None:
        rules = (ROOT / "debian/rules").read_text(encoding="utf-8")
        self.assertIn(
            "$(MAKE) install DESTDIR=$(CURDIR)/debian/ghelper-linux", rules
        )

    def test_debian_runtime_dependencies_are_declared(self) -> None:
        control = (ROOT / "debian/control").read_text(encoding="utf-8")
        depends = _deb_field(control, "Depends")
        for dependency in (
            "python3",
            "python3-gi",
            "python3-gi-cairo",
            "python3-dbus",
            "gir1.2-gtk-4.0",
            "gir1.2-adw-1",
            "gir1.2-gdkpixbuf-2.0",
            "asusctl",
            "cardwire",
        ):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, depends)
        self.assertIn("pkexec", _deb_field(control, "Recommends"))

    def test_rpm_runtime_dependencies_are_declared(self) -> None:
        spec = (ROOT / "packaging/ghelper-linux.spec").read_text(encoding="utf-8")
        requires = _spec_deps(spec, "Requires")
        for dependency in (
            "python3",
            "python3-gobject",
            "python3-dbus",
            "gtk4",
            "libadwaita",
            "gdk-pixbuf2",
            "asusctl",
            "cardwire",
        ):
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, requires)
        self.assertIn("polkit", _spec_deps(spec, "Recommends"))


class PolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ET.parse(ROOT / f"data/{app_id()}.policy").getroot()

    def test_action_id_is_namespaced(self) -> None:
        action = self.root.find("action")
        assert action is not None
        self.assertEqual(
            action.get("id"), "io.github.ghelperlinux.ghelper.rescan-pci"
        )

    def test_policy_uses_the_install_prefix_placeholder(self) -> None:
        annotation = self.root.find(
            "action/annotate[@key='org.freedesktop.policykit.exec.path']"
        )
        assert annotation is not None
        # The installers substitute the real prefix, so /usr/local works too.
        self.assertEqual(annotation.text, "@LIBEXECDIR@/rescan-pci")

    def test_helper_is_packaged(self) -> None:
        spec = (ROOT / "packaging/ghelper-linux.spec").read_text(encoding="utf-8")
        self.assertIn("%{_libexecdir}/ghelper-linux/rescan-pci", spec)
        self.assertIn("%{_datadir}/polkit-1/actions/", spec)


if __name__ == "__main__":
    unittest.main()
