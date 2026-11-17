#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install without make (useful on minimal systems).
set -euo pipefail

PREFIX="${PREFIX:-/usr}"
DESTDIR="${DESTDIR:-}"
APPID="io.github.ghelperlinux.GHelper"
HERE="$(cd "$(dirname "$0")" && pwd)"

LIBDIR="$DESTDIR$PREFIX/lib/ghelper-linux"
BINDIR="$DESTDIR$PREFIX/bin"
SHAREDIR="$DESTDIR$PREFIX/share"

install -d "$LIBDIR" "$BINDIR"
rm -rf "$LIBDIR/ghelper"
cp -r "$HERE/src/ghelper" "$LIBDIR/ghelper"
find "$LIBDIR" -name __pycache__ -type d -prune -exec rm -rf {} +

cat > "$BINDIR/ghelper-linux" <<EOF
#!/bin/sh
exec env PYTHONPATH="$PREFIX/lib/ghelper-linux" python3 -m ghelper "\$@"
EOF
chmod 755 "$BINDIR/ghelper-linux"

install -Dm644 "$HERE/data/$APPID.desktop" "$SHAREDIR/applications/$APPID.desktop"
install -Dm644 "$HERE/data/$APPID.metainfo.xml" "$SHAREDIR/metainfo/$APPID.metainfo.xml"
install -Dm644 "$HERE/docs/ghelper-linux.1" "$SHAREDIR/man/man1/ghelper-linux.1"
install -Dm644 "$HERE/src/ghelper/data/$APPID.svg" \
    "$SHAREDIR/icons/hicolor/scalable/apps/$APPID.svg"
install -Dm644 "$HERE/src/ghelper/data/$APPID.png" \
    "$SHAREDIR/icons/hicolor/128x128/apps/$APPID.png"

install -Dm644 "$HERE/data/ghelper-automation.service" \
    "$DESTDIR$PREFIX/lib/systemd/user/ghelper-automation.service"
install -Dm644 "$HERE/data/60-ghelper-hotkeys.rules" \
    "$DESTDIR$PREFIX/lib/udev/rules.d/60-ghelper-hotkeys.rules"
install -Dm755 "$HERE/data/ghelper-rescan-pci" \
    "$DESTDIR$PREFIX/libexec/ghelper-linux/rescan-pci"
install -Dm644 "$HERE/data/$APPID.policy" \
    "$DESTDIR$PREFIX/share/polkit-1/actions/$APPID.policy"

if [ -z "$DESTDIR" ]; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --subsystem-match=input 2>/dev/null || true
    gtk-update-icon-cache -f -t "$SHAREDIR/icons/hicolor" 2>/dev/null || true
fi
echo "Installed ghelper-linux to $PREFIX"
