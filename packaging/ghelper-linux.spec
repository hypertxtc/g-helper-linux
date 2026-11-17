# SPDX-License-Identifier: GPL-3.0-or-later

Name:           ghelper-linux
Version:        %{?gh_version}%{!?gh_version:0.2.0}
Release:        1%{?dist}
Summary:        G-Helper style control centre for ASUS laptops on Linux

License:        GPL-3.0-or-later
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3-gobject
Requires:       python3-dbus
Requires:       gtk4
Requires:       libadwaita
Requires:       asusctl
Recommends:     cardwire

BuildRequires:  python3
BuildRequires:  python3-gobject
BuildRequires:  python3-dbus
BuildRequires:  gtk4
BuildRequires:  libadwaita
BuildRequires:  desktop-file-utils
BuildRequires:  appstream

%description
A small GTK4/libadwaita front end for asusd and cardwire. Provides performance
profiles, custom fan curves, power limits, GPU modes, firmware GPU switches,
panel overdrive, refresh-rate control and battery charge limits for ASUS
laptops.

%prep
%setup -q

%build

%install
make install DESTDIR=%{buildroot} PREFIX=/usr

%check
export PYTHONPATH="$PWD/src"
%{__python3} -m unittest discover -s tests -v
desktop-file-validate \
    %{buildroot}%{_datadir}/applications/io.github.ghelperlinux.GHelper.desktop
# Exit code 3 means warnings (currently the missing homepage URL) only.
appstreamcli validate --no-net \
    %{buildroot}%{_datadir}/metainfo/io.github.ghelperlinux.GHelper.metainfo.xml \
    || test $? -eq 3

%files
%license LICENSE
%doc README.md
%{_bindir}/ghelper-linux
%{_prefix}/lib/ghelper-linux
%{_datadir}/applications/io.github.ghelperlinux.GHelper.desktop
%{_datadir}/metainfo/io.github.ghelperlinux.GHelper.metainfo.xml
%{_mandir}/man1/ghelper-linux.1*
%{_datadir}/icons/hicolor/scalable/apps/io.github.ghelperlinux.GHelper.svg
%{_datadir}/icons/hicolor/128x128/apps/io.github.ghelperlinux.GHelper.png
%{_prefix}/lib/systemd/user/ghelper-automation.service
%{_udevrulesdir}/60-ghelper-hotkeys.rules
%{_libexecdir}/ghelper-linux/rescan-pci
%{_datadir}/polkit-1/actions/io.github.ghelperlinux.GHelper.policy

%changelog
* Tue Nov 17 2026 George <george@localhost> - 0.2.0-1
- Fn / ROG hotkey reading and mapping with a bundled udev rule
- Window opens next to the KDE panel on any screen edge
- Fan-curve presets, copy between profiles, JSON import/export
- AppStream metadata

* Tue Nov 17 2026 George <george@localhost> - 0.1.0-1
- Initial package
