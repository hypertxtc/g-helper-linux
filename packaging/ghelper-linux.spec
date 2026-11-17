# SPDX-License-Identifier: GPL-3.0-or-later

Name:           ghelper-linux
Version:        %{?gh_version}%{!?gh_version:0.2.2}
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
# The metainfo has its homepage and bug tracker, so validation must pass
# outright; the only remaining note is pedantic (uppercase component ID).
appstreamcli validate --no-net \
    %{buildroot}%{_datadir}/metainfo/io.github.ghelperlinux.GHelper.metainfo.xml

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
* Tue Nov 17 2026 George <george@localhost> - 0.2.2-1
- Tray menu switches GPU modes: Eco, Standard, Smart and Optimized
- Boot sound toggle in Settings -> Startup
- GPU section and tray tooltip show when a reboot is required

* Tue Nov 17 2026 George <george@localhost> - 0.2.1-1
- Smart GPU mode: keep the dGPU blocked except for launched apps
- Main window names Smart and Manual instead of claiming Standard
- AC/battery automation leaves Smart and Manual modes alone
- GPU decisions are unit tested; no UI stalls or raw cardwire errors
  when the dGPU is absent
- Installing no longer rewrites the tracked icon

* Tue Nov 17 2026 George <george@localhost> - 0.2.0-1
- Silent / Balanced / Turbo profiles with editable fan curves
- Fan-curve presets, copy between profiles, JSON import/export
- cardwire GPU modes: Eco and Standard switch at runtime, no reboot
- Firmware GPU MUX and dGPU switches, plus a Rescan dGPU action
- Battery charge limit, health, and live charge/wattage/runtime readouts
- Keyboard Aura lighting and animation speed
- Fn / ROG hotkey reading and mapping with a bundled udev rule
- Refresh-rate switching and per-app dGPU launching
- Window opens next to the KDE panel on any screen edge
- AppStream metadata and a man page

* Tue Nov 17 2026 George <george@localhost> - 0.1.0-1
- Initial package
