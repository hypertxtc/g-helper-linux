#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build a headless Ubuntu 24.04 .deb of asusctl (asusd + asusctl + asus-shutdown)
# without the GUI rog-control-center dependencies.
set -euo pipefail

REPO_URL="https://github.com/OpenGamingCollective/asusctl.git"
LATEST_API="https://api.github.com/repos/OpenGamingCollective/asusctl/releases/latest"
VERSION="6.5.0"
OUTPUT_DIR="./dist"
WORK_DIR=""
OWN_WORK=0

usage() {
    cat <<'EOF'
Usage: build-asusctl-deb.sh [OPTIONS]

Build a headless .deb containing asusd, asusctl, and asus-shutdown.

Options:
  --version <tag>   Git tag or release version to build (default: 6.5.0;
                    pass "latest" to query GitHub releases)
  --output <dir>    Directory for the built .deb (default: ./dist)
  --work-dir <dir>  Build scratch directory (default: temporary dir)
  -h, --help        Show this help message
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            VERSION="${2:?--version requires a value}"
            shift 2
            ;;
        --output)
            OUTPUT_DIR="${2:?--output requires a value}"
            shift 2
            ;;
        --work-dir)
            WORK_DIR="${2:?--work-dir requires a value}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

cleanup() {
    if [ "$OWN_WORK" -eq 1 ] && [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

if [ -z "$WORK_DIR" ]; then
    WORK_DIR="$(mktemp -d -t asusctl-deb-XXXXXX)"
    OWN_WORK=1
else
    mkdir -p "$WORK_DIR"
fi

if [ "$VERSION" = "latest" ]; then
    VERSION="$(python3 - "$LATEST_API" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request(sys.argv[1], headers={"User-Agent": "g-helper-linux-builder"})
with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.load(resp)
tag = (data.get("tag_name") or "").strip()
if not tag:
    raise SystemExit("could not resolve latest asusctl release tag")
print(tag)
PY
)"
fi

ensure_rust() {
    if [ -d "$HOME/.cargo/bin" ]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
    local ok=0
    if command -v rustc >/dev/null 2>&1 && command -v cargo >/dev/null 2>&1; then
        if python3 - "$(rustc --version)" <<'PY'
import re, sys
m = re.search(r"(\d+)\.(\d+)", sys.argv[1])
if not m:
    raise SystemExit(1)
ver = (int(m.group(1)), int(m.group(2)))
raise SystemExit(0 if ver >= (1, 93) else 1)
PY
        then
            ok=1
        fi
    fi

    if [ "$ok" -eq 0 ]; then
        if command -v rustup >/dev/null 2>&1; then
            echo "Updating Rust toolchain via rustup (requires >= 1.93)..." >&2
            rustup toolchain install stable --profile minimal --no-self-update >&2
            rustup default stable >&2
        else
            echo "Installing Rust toolchain via rustup (requires >= 1.93)..." >&2
            curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
                | sh -s -- -y --profile minimal --no-modify-path >&2
            export PATH="$HOME/.cargo/bin:$PATH"
        fi
    fi
    rustc --version >&2
    cargo --version >&2
}

ensure_rust

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

SRC_DIR="$WORK_DIR/src"
PKG_DIR="$WORK_DIR/pkg"
rm -rf "$SRC_DIR" "$PKG_DIR"

echo "Cloning asusctl $VERSION..." >&2
git clone --depth 1 --branch "$VERSION" "$REPO_URL" "$SRC_DIR" >&2

echo "Building headless asusd, asusctl, and asus-shutdown..." >&2
(
    cd "$SRC_DIR"
    cargo build --release --locked -p asusd -p asusctl -p asus-shutdown >&2
    if command -v strip >/dev/null 2>&1; then
        strip -s target/release/asusd target/release/asusctl target/release/asus-shutdown || true
    fi
)

echo "Staging Debian package contents..." >&2
install -Dm755 "$SRC_DIR/target/release/asusd" "$PKG_DIR/usr/bin/asusd"
install -Dm755 "$SRC_DIR/target/release/asusctl" "$PKG_DIR/usr/bin/asusctl"
install -Dm755 "$SRC_DIR/target/release/asus-shutdown" "$PKG_DIR/usr/bin/asus-shutdown"

install -Dm644 "$SRC_DIR/data/asusd.service" "$PKG_DIR/usr/lib/systemd/system/asusd.service"
install -Dm644 "$SRC_DIR/data/asus-shutdown.service" \
    "$PKG_DIR/usr/lib/systemd/system/asus-shutdown.service"
install -Dm644 "$SRC_DIR/data/asusd.rules" "$PKG_DIR/usr/lib/udev/rules.d/99-asusd.rules"
install -Dm644 "$SRC_DIR/data/asusd.conf" "$PKG_DIR/usr/share/dbus-1/system.d/asusd.conf"
install -Dm644 "$SRC_DIR/rog-aura/data/aura_support.ron" \
    "$PKG_DIR/usr/share/asusd/aura_support.ron"
install -Dm644 "$SRC_DIR/LICENSE" "$PKG_DIR/usr/share/doc/asusctl/LICENSE"

if [ -d "$SRC_DIR/rog-anime/data/anime" ]; then
    (
        cd "$SRC_DIR/rog-anime/data"
        find anime -type f -exec install -Dm644 "{}" "$PKG_DIR/usr/share/asusd/{}" \;
    )
fi

ARCH="$(dpkg --print-architecture)"
DEB_VERSION="${VERSION#v}-1~ubuntu24.04"
OUT_DEB="$OUTPUT_DIR/asusctl_${DEB_VERSION}_${ARCH}.deb"

install -d "$PKG_DIR/DEBIAN"
cat > "$PKG_DIR/DEBIAN/control" <<EOF
Package: asusctl
Version: $DEB_VERSION
Architecture: $ARCH
Maintainer: g-helper-linux <atlas@hypertextcoffee.com>
Section: utils
Priority: optional
Depends: libc6 (>= 2.38), libudev1, libusb-1.0-0, dbus, udev
Provides: asusd
Description: ASUS laptop control daemon and CLI (headless build for Ubuntu 24.04)
 Headless build of asusd, asusctl, and asus-shutdown from OpenGamingCollective/asusctl
 packaged for Ubuntu 24.04 derivatives (such as Zorin OS 18) used by ghelper-linux.
EOF

cat > "$PKG_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules || true
    fi
    if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || true
        systemctl enable asus-shutdown.service || true
        systemctl try-restart asusd.service || systemctl start asusd.service || true
    fi
fi
exit 0
EOF
chmod 0755 "$PKG_DIR/DEBIAN/postinst"

cat > "$PKG_DIR/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ] && [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    systemctl stop asusd.service asus-shutdown.service || true
fi
exit 0
EOF
chmod 0755 "$PKG_DIR/DEBIAN/prerm"

dpkg-deb --build --root-owner-group "$PKG_DIR" "$OUT_DEB" >&2
echo "$OUT_DEB"
