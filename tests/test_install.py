# SPDX-License-Identifier: GPL-3.0-or-later

"""The Makefile and install.sh must install the same files.

They exist separately (make is not always available), so a file added to one
and forgotten in the other would silently ship a broken package.  These checks
also pin the prefix handling of the privileged rescan helper: polkit matches
its exact path, so the policy has to be generated with the install prefix.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCE_RE = re.compile(r"(?<![\w])(?:src/ghelper/data|data|docs)/[A-Za-z0-9_.$()/{}-]+")


def _makefile() -> str:
    return (ROOT / "Makefile").read_text(encoding="utf-8")


def _var(text: str, name: str) -> str:
    match = re.search(rf"^{name}\s*:?=\s*(\S+)", text, re.MULTILINE)
    assert match is not None, f"Makefile has no {name}"
    return match.group(1)


def _target(text: str, name: str) -> str:
    """Recipe of ``name`` (up to the next target at column 0)."""
    match = re.search(
        rf"^{name}:[^\n]*\n(.*?)(?=^\S)", text, re.MULTILINE | re.DOTALL
    )
    assert match is not None, f"Makefile has no {name} target"
    return match.group(1)


def _normalise(text: str) -> str:
    makefile = _makefile()
    app_id = _var(makefile, "APPID")
    app_name = _var(makefile, "NAME")
    for variable, value in (("APPID", app_id), ("NAME", app_name)):
        for form in (f"$({variable})", f"${{{variable}}}", f"${variable}"):
            text = text.replace(form, value)
    return text


def sources(text: str) -> set[str]:
    """Source paths installed by a recipe, with Makefile variables expanded."""
    return {_normalise(match.group(0).rstrip(".-")) for match in SOURCE_RE.finditer(text)}


class InstallerParityTests(unittest.TestCase):
    def test_makefile_names_the_app_it_installs(self) -> None:
        self.assertIn("ghelper", _var(_makefile(), "NAME"))

    def test_both_installers_install_the_same_sources(self) -> None:
        make_install = sources(_target(_makefile(), "install"))
        shell_install = sources((ROOT / "install.sh").read_text(encoding="utf-8"))
        self.assertEqual(
            make_install,
            shell_install,
            "Makefile and install.sh disagree about installed files",
        )

    def test_every_shipped_data_file_is_installed(self) -> None:
        make_install = sources(_target(_makefile(), "install"))
        shipped = {
            f"data/{path.name}" for path in (ROOT / "data").iterdir() if path.is_file()
        }
        self.assertEqual(shipped - make_install, set())
        self.assertIn("docs/ghelper-linux.1", make_install)

    def test_uninstall_removes_every_install_directory(self) -> None:
        text = _makefile()
        uninstall = _target(text, "uninstall")
        for variable in ("LIBDIR", "BINDIR", "LIBEXECDIR", "POLICYDIR", "UNITDIR", "UDEVDIR"):
            with self.subTest(directory=variable):
                self.assertIn(f"$({variable})", uninstall)


class PrefixTests(unittest.TestCase):
    def test_policy_is_generated_with_the_install_prefix(self) -> None:
        policy = (ROOT / "data/io.github.ghelperlinux.GHelper.policy").read_text(
            encoding="utf-8"
        )
        self.assertIn("@LIBEXECDIR@/rescan-pci", policy)
        for installer in ("Makefile", "install.sh"):
            with self.subTest(installer=installer):
                text = (ROOT / installer).read_text(encoding="utf-8")
                self.assertIn("@LIBEXECDIR@", text)
                self.assertIn("libexec/ghelper-linux", text)

    def test_launcher_exports_the_helper_directory(self) -> None:
        for installer in ("Makefile", "install.sh"):
            with self.subTest(installer=installer):
                text = (ROOT / installer).read_text(encoding="utf-8")
                self.assertIn("GHELPER_LIBEXECDIR", text)

    def test_automation_unit_finds_the_binary_through_path(self) -> None:
        unit = (ROOT / "data/ghelper-automation.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/env ghelper-linux --daemon", unit)


if __name__ == "__main__":
    unittest.main()
