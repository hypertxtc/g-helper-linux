#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# One-command prerequisite and package installer for g-helper-linux.
set -euo pipefail

REPO_SLUG="hypertxtc/g-helper-linux"
CARDWIRE_REPO="OpenGamingCollective/cardwire"
RAW_BASE="https://raw.githubusercontent.com/${REPO_SLUG}/main"
GHELPER_API="https://api.github.com/repos/${REPO_SLUG}/releases/latest"
CARDWIRE_API="https://api.github.com/repos/${CARDWIRE_REPO}/releases/latest"

DRY_RUN=0
ASSUME_YES=0
NO_LAUNCH=0
BUILD_ASUSCTL=0
LOCAL_DIR=""
NEEDS_REBOOT=0
WAS_INSTALLED=0
WORK_DIR=""

usage() {
    cat <<'EOF'
Usage: setup.sh [OPTIONS]

Install prerequisites (asusctl, cardwire, GTK4/libadwaita) and g-helper-linux,
enable system services, configure kernel BPF LSM if needed, and launch Setup Check.

Options:
  --dry-run         Show detected platform and planned actions without changes
  -y, --yes         Accept prompts non-interactively
  --no-launch       Do not launch ghelper-linux after installation
  --build-asusctl   Force building the headless asusctl .deb locally (Ubuntu/Zorin)
  --local-dir DIR   Prefer local packages or checkout in DIR before GitHub releases
  -h, --help        Show this help message
EOF
}

log() {
    printf '==> %s\n' "$*"
}

warn() {
    printf 'WARNING: %s\n' "$*" >&2
}

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

run_cmd() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[dry-run]'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

run_sudo() {
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[dry-run] sudo'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    sudo "$@"
}

confirm() {
    local prompt="$1"
    if [ "$ASSUME_YES" -eq 1 ] || [ "$DRY_RUN" -eq 1 ]; then
        return 0
    fi
    if [ -r /dev/tty ]; then
        local reply=""
        printf '%s [Y/n] ' "$prompt" >/dev/tty
        IFS= read -r reply </dev/tty || true
        case "$reply" in
            ""|[Yy]|[Yy][Ee][Ss]) return 0 ;;
            *) return 1 ;;
        esac
    fi
    return 0
}

cleanup() {
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            -y|--yes)
                ASSUME_YES=1
                shift
                ;;
            --no-launch)
                NO_LAUNCH=1
                shift
                ;;
            --build-asusctl)
                BUILD_ASUSCTL=1
                shift
                ;;
            --local-dir)
                LOCAL_DIR="${2:?--local-dir requires a directory path}"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $1 (try --help)"
                ;;
        esac
    done
}

find_local_file() {
    local pattern="$1"
    if [ -z "$LOCAL_DIR" ] || [ ! -d "$LOCAL_DIR" ]; then
        return 1
    fi
    local dir
    for dir in "$LOCAL_DIR" "$LOCAL_DIR/dist" "$LOCAL_DIR/build"; do
        if [ -d "$dir" ]; then
            local match
            match="$(find "$dir" -maxdepth 1 -type f -name "$pattern" | sort -V | tail -n 1 || true)"
            if [ -n "$match" ]; then
                printf '%s\n' "$match"
                return 0
            fi
        fi
    done
    return 1
}

resolve_release_asset() {
    local api_url="$1"
    local regex="$2"
    python3 - "$api_url" "$regex" <<'PY'
import json, re, sys, urllib.request

api_url, pattern = sys.argv[1], sys.argv[2]
req = urllib.request.Request(api_url, headers={"User-Agent": "g-helper-linux-setup"})
with urllib.request.urlopen(req, timeout=8) as resp:
    data = json.load(resp)
rx = re.compile(pattern)
for asset in data.get("assets") or []:
    name = asset.get("name") or ""
    if rx.match(name):
        print(asset["browser_download_url"])
        raise SystemExit(0)
raise SystemExit(1)
PY
}

download_asset() {
    local api_url="$1"
    local regex="$2"
    local fallback_label="$3"
    local url=""

    if ! url="$(resolve_release_asset "$api_url" "$regex" 2>/dev/null)"; then
        if [ "$DRY_RUN" -eq 1 ]; then
            printf '%s/PLACEHOLDER_%s\n' "$WORK_DIR" "$fallback_label"
            return 0
        fi
        return 1
    fi

    local dest="$WORK_DIR/$(basename "$url")"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '[dry-run] download %s -> %s\n' "$url" "$dest" >&2
        printf '%s\n' "$dest"
        return 0
    fi
    log "Downloading $(basename "$url")..." >&2
    curl -fsSL -o "$dest" "$url"
    chmod 644 "$dest"
    printf '%s\n' "$dest"
}

load_os_release() {
    [ -r /etc/os-release ] || die "Cannot read /etc/os-release to detect Linux distribution."
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_ID_LIKE="${ID_LIKE:-}"
    OS_VERSION_ID="${VERSION_ID:-}"
    OS_CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    OS_PRETTY="${PRETTY_NAME:-$OS_ID}"

    case "$(uname -m)" in
        x86_64|amd64)
            DEB_ARCH="amd64"
            RPM_ARCH="x86_64"
            ;;
        aarch64|arm64)
            DEB_ARCH="arm64"
            RPM_ARCH="aarch64"
            ;;
        *)
            die "Unsupported CPU architecture: $(uname -m)"
            ;;
    esac
}

is_debian_family() {
    case " $OS_ID $OS_ID_LIKE " in
        *" zorin "*|*" ubuntu "*|*" debian "*|*" linuxmint "*|*" pop "*) return 0 ;;
        *) return 1 ;;
    esac
}

is_fedora_family() {
    case " $OS_ID $OS_ID_LIKE " in
        *" fedora "*) return 0 ;;
        *) return 1 ;;
    esac
}

is_arch_family() {
    case " $OS_ID $OS_ID_LIKE " in
        *" arch "*|*" cachyos "*|*" endeavouros "*|*" manjaro "*) return 0 ;;
        *) return 1 ;;
    esac
}

deb_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

install_debian_family() {
    if [ "$OS_ID" = "zorin" ] && [[ "$OS_VERSION_ID" == 17* ]]; then
        die "Zorin OS 17 (Ubuntu 22.04 base) lacks the GTK 4.14 / libadwaita 1.5 and cardwire .deb requirements. Zorin OS 18+ is required."
    fi
    if [ "$OS_CODENAME" = "jammy" ] || [ "$OS_CODENAME" = "focal" ]; then
        die "Ubuntu 24.04 (noble) or newer is required (detected codename: $OS_CODENAME)."
    fi

    log "Detected Debian/Ubuntu family: $OS_PRETTY ($DEB_ARCH)"
    run_sudo apt-get update
    run_sudo apt-get install -y curl ca-certificates python3 pkexec

    local debs=()

    if deb_installed cardwire; then
        log "cardwire is already installed."
    else
        local cw_deb=""
        if cw_deb="$(find_local_file "cardwire_*_${DEB_ARCH}.deb")"; then
            log "Using local cardwire package: $cw_deb"
        else
            cw_deb="$(download_asset "$CARDWIRE_API" "^cardwire_.*_${DEB_ARCH}\\.deb$" "cardwire.deb")" \
                || die "Could not find cardwire .deb for $DEB_ARCH on OpenGamingCollective/cardwire releases."
        fi
        debs+=("$cw_deb")
    fi

    if [ "$BUILD_ASUSCTL" -eq 0 ] && deb_installed asusctl; then
        log "asusctl is already installed."
    else
        local asus_deb=""
        if [ "$BUILD_ASUSCTL" -eq 0 ] && asus_deb="$(find_local_file "asusctl_*_${DEB_ARCH}.deb")"; then
            log "Using local asusctl package: $asus_deb"
        elif [ "$BUILD_ASUSCTL" -eq 0 ] \
            && asus_deb="$(download_asset "$GHELPER_API" "^asusctl_.*_${DEB_ARCH}\\.deb$" "asusctl.deb")"; then
            :
        else
            log "No prebuilt asusctl .deb found on latest release; building headless asusctl .deb locally..."
            run_sudo apt-get install -y --no-install-recommends \
                build-essential pkg-config libudev-dev libusb-1.0-0-dev \
                libclang-dev dpkg-dev git
            local script_dir="${BASH_SOURCE[0]:-}"
            local builder=""
            if [ -n "$script_dir" ] && [ -f "$(dirname "$script_dir")/packaging/build-asusctl-deb.sh" ]; then
                builder="$(dirname "$script_dir")/packaging/build-asusctl-deb.sh"
            elif [ -n "$LOCAL_DIR" ] && [ -f "$LOCAL_DIR/packaging/build-asusctl-deb.sh" ]; then
                builder="$LOCAL_DIR/packaging/build-asusctl-deb.sh"
            else
                builder="$WORK_DIR/build-asusctl-deb.sh"
                if [ "$DRY_RUN" -eq 0 ]; then
                    curl -fsSL -o "$builder" "$RAW_BASE/packaging/build-asusctl-deb.sh"
                    chmod +x "$builder"
                fi
            fi
            if [ "$DRY_RUN" -eq 1 ]; then
                printf '[dry-run] bash %q --output %q\n' "$builder" "$WORK_DIR"
                asus_deb="$WORK_DIR/PLACEHOLDER_asusctl.deb"
            else
                asus_deb="$(bash "$builder" --output "$WORK_DIR")"
            fi
        fi
        debs+=("$asus_deb")
    fi

    local gh_deb=""
    if gh_deb="$(find_local_file "ghelper-linux_*_all.deb")"; then
        log "Using local ghelper-linux package: $gh_deb"
    else
        gh_deb="$(download_asset "$GHELPER_API" "^ghelper-linux_.*_all\\.deb$" "ghelper-linux.deb")" \
            || die "Could not find ghelper-linux .deb on ${REPO_SLUG} releases."
    fi
    debs+=("$gh_deb")

    log "Installing packages via apt..."
    run_sudo apt-get install -y "${debs[@]}"
}

fedora_has_prereqs() {
    if rpm -q asusctl >/dev/null 2>&1 && rpm -q cardwire >/dev/null 2>&1; then
        return 0
    fi
    dnf -q -C info asusctl >/dev/null 2>&1 && dnf -q -C info cardwire >/dev/null 2>&1
}

install_fedora_family() {
    log "Detected Fedora family: $OS_PRETTY ($RPM_ARCH)"

    if ! fedora_has_prereqs; then
        if ! ls /etc/yum.repos.d/*terra* >/dev/null 2>&1 \
            && ! dnf -q repolist --enabled 2>/dev/null | grep -qi terra; then
            log "asusctl or cardwire is not currently in enabled repositories."
            if confirm "Add the Terra repository for asusctl and cardwire?"; then
                local ver="${OS_VERSION_ID:-rawhide}"
                run_sudo dnf install -y --nogpgcheck \
                    --repofrompath "terra,https://repos.fyralabs.com/terra${ver}" \
                    terra-release
            else
                die "Terra repository (providing asusctl and cardwire) is required on Fedora."
            fi
        fi
    fi

    local gh_rpm=""
    if gh_rpm="$(find_local_file "ghelper-linux-*.noarch.rpm")"; then
        log "Using local ghelper-linux RPM: $gh_rpm"
    elif [ -d "$HOME/rpmbuild/RPMS/noarch" ] && [ -n "$LOCAL_DIR" ]; then
        gh_rpm="$(find "$HOME/rpmbuild/RPMS/noarch" -maxdepth 1 -type f -name "ghelper-linux-*.noarch.rpm" | sort -V | tail -n 1 || true)"
    fi
    if [ -z "$gh_rpm" ]; then
        gh_rpm="$(download_asset "$GHELPER_API" "^ghelper-linux-.*\\.noarch\\.rpm$" "ghelper-linux.rpm")" \
            || die "Could not find ghelper-linux RPM on ${REPO_SLUG} releases."
    fi

    log "Installing ghelper-linux and prerequisites via dnf..."
    run_sudo dnf install -y "$gh_rpm"
}

install_arch_family() {
    log "Detected Arch Linux family: $OS_PRETTY"
    run_sudo pacman -S --needed --noconfirm \
        python-gobject python-dbus gtk4 libadwaita asusctl

    if ! command -v cardwire >/dev/null 2>&1; then
        warn "cardwire is not installed yet. Install the 'cardwire' package from AUR / OpenGamingCollective (https://opengamingcollective.github.io/cardwire) for GPU switching."
    fi

    local script_dir="${BASH_SOURCE[0]:-}"
    local src_root=""
    if [ -n "$LOCAL_DIR" ] && [ -f "$LOCAL_DIR/install.sh" ]; then
        src_root="$LOCAL_DIR"
    elif [ -n "$script_dir" ] && [ -f "$(dirname "$script_dir")/install.sh" ]; then
        src_root="$(dirname "$script_dir")"
    else
        src_root="$WORK_DIR/g-helper-linux-main"
        if [ "$DRY_RUN" -eq 1 ]; then
            printf '[dry-run] download source tarball and run sudo ./install.sh\n'
            return 0
        fi
        curl -fsSL "https://github.com/${REPO_SLUG}/archive/refs/heads/main.tar.gz" \
            | tar -xz -C "$WORK_DIR"
    fi
    run_sudo "$src_root/install.sh"
}

configure_services_and_lsm() {
    log "Reloading udev rules and systemd units..."
    run_sudo udevadm control --reload-rules || true
    run_sudo udevadm trigger --subsystem-match=input || true
    run_sudo systemctl daemon-reload

    log "Enabling system services..."
    run_sudo systemctl enable asus-shutdown.service 2>/dev/null || true
    run_sudo systemctl start asusd.service || true
    run_sudo systemctl enable cardwired.service 2>/dev/null || true

    local active_lsm=""
    active_lsm="$(cat /sys/kernel/security/lsm 2>/dev/null || true)"
    case ",${active_lsm}," in
        *,bpf,*)
            log "Kernel BPF LSM is active ($active_lsm). Starting cardwired..."
            run_sudo systemctl start cardwired.service || true
            ;;
        *)
            warn "Kernel BPF LSM is NOT currently active (${active_lsm:-none}). Cardwire requires 'bpf' in the kernel LSM list."
            local grub_cfg="/etc/default/grub.d/90-ghelper-cardwire-lsm.cfg"
            if [ -f "$grub_cfg" ]; then
                log "GRUB BPF LSM snippet already exists ($grub_cfg)."
                NEEDS_REBOOT=1
            elif [ -d /etc/default/grub.d ] && command -v update-grub >/dev/null 2>&1 \
                && [ -n "$active_lsm" ] && [[ "$active_lsm" =~ ^[a-z0-9_,]+$ ]]; then
                if confirm "Configure GRUB ($grub_cfg) to enable BPF LSM alongside existing security modules (${active_lsm})?"; then
                    if [ "$DRY_RUN" -eq 1 ]; then
                        printf '[dry-run] write %s with lsm=%s,bpf\n' "$grub_cfg" "$active_lsm"
                    else
                        printf '# Added by g-helper-linux to enable kernel BPF LSM for cardwire.\nGRUB_CMDLINE_LINUX_DEFAULT="${GRUB_CMDLINE_LINUX_DEFAULT:-} lsm=%s,bpf"\n' \
                            "$active_lsm" | sudo tee "$grub_cfg" >/dev/null
                        sudo chmod 644 "$grub_cfg"
                    fi
                    run_sudo update-grub
                    NEEDS_REBOOT=1
                else
                    warn "Skipped GRUB edit. Add lsm=${active_lsm},bpf to your kernel command line and reboot."
                    NEEDS_REBOOT=1
                fi
            else
                warn "Add lsm=${active_lsm:+${active_lsm},}bpf to your bootloader kernel parameters and reboot once."
                NEEDS_REBOOT=1
            fi
            ;;
    esac
}

report_environment() {
    local session="${XDG_SESSION_TYPE:-unknown}"
    if [ "$session" = "x11" ]; then
        cat <<'EOF'

----------------------------------------------------------------------
IMPORTANT — Wayland required for GPU mode switching (cardwire):
You are currently logged into an X11 session.
To switch on Zorin OS / GNOME:
  1. Log out of your current session
  2. Click your username on the login screen
  3. Click the gear icon in the bottom-right corner
  4. Choose "Zorin Desktop on Wayland" and sign back in
----------------------------------------------------------------------

EOF
    fi

    local nv_info=""
    nv_info="$(python3 - <<'PY' 2>/dev/null || true
import ctypes, os
pci_root = "/sys/bus/pci/devices"
if not os.path.isdir(pci_root):
    raise SystemExit(0)
devs = []
for entry in sorted(os.listdir(pci_root)):
    p = os.path.join(pci_root, entry)
    try:
        cls = open(os.path.join(p, "class")).read().strip()
        ven = open(os.path.join(p, "vendor")).read().strip().lower()
    except OSError:
        continue
    try:
        if int(cls, 16) >> 16 != 0x03 or ven != "0x10de":
            continue
    except ValueError:
        continue
    drv_link = os.path.join(p, "driver")
    drv = os.path.basename(os.path.realpath(drv_link)) if os.path.islink(drv_link) else "none"
    devs.append((entry, drv))
if not devs:
    raise SystemExit(0)
nvml = False
for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
    try:
        ctypes.CDLL(name)
        nvml = True
        break
    except OSError:
        pass
print("; ".join(f"{pci} (driver={drv}, nvml={'yes' if nvml else 'no'})" for pci, drv in devs))
PY
)"
    if [ -n "$nv_info" ]; then
        log "Detected NVIDIA GPU: $nv_info"
    fi
}

stop_running_ghelper() {
    if [ "$DRY_RUN" -eq 1 ] || [ "$WAS_INSTALLED" -ne 1 ]; then
        return 0
    fi
    if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
        return 0
    fi
    log "Stopping any running ghelper-linux instance before relaunch..."
    gdbus call --session \
        --dest io.github.ghelperlinux.GHelper \
        --object-path /io/github/ghelperlinux/GHelper \
        --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1 \
        || ghelper-linux --quit >/dev/null 2>&1 \
        || true

    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if ! python3 -c 'import dbus; raise SystemExit(0 if dbus.SessionBus().name_has_owner("io.github.ghelperlinux.GHelper") else 1)' 2>/dev/null; then
            break
        fi
        sleep 0.2
    done
}

main() {
    parse_args "$@"

    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        die "Do not run setup.sh as root. Run it as your normal user; it will invoke sudo when needed."
    fi

    if command -v ghelper-linux >/dev/null 2>&1; then
        WAS_INSTALLED=1
    fi

    trap cleanup EXIT
    WORK_DIR="$(mktemp -d -t ghelper-setup-XXXXXX)"
    chmod 755 "$WORK_DIR"

    load_os_release
    if is_debian_family; then
        install_debian_family
    elif is_fedora_family; then
        install_fedora_family
    elif is_arch_family; then
        install_arch_family
    else
        die "Unsupported distribution ($OS_PRETTY). Install dependencies from README.md and run sudo ./install.sh."
    fi

    configure_services_and_lsm
    stop_running_ghelper
    report_environment

    echo ""
    if [ "$NEEDS_REBOOT" -eq 1 ]; then
        log "Installation complete — please REBOOT ONCE to activate Kernel BPF LSM, then open G-Helper from your app menu."
    else
        log "Installation complete! Launch G-Helper from your app menu or run: ghelper-linux"
        if [ "$NO_LAUNCH" -eq 0 ] && [ "$DRY_RUN" -eq 0 ] \
            && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; } \
            && command -v ghelper-linux >/dev/null 2>&1; then
            if [ "$WAS_INSTALLED" -eq 1 ]; then
                log "Launching updated G-Helper..."
                nohup ghelper-linux >/dev/null 2>&1 &
            else
                log "Opening G-Helper Setup Check..."
                nohup ghelper-linux --page setup >/dev/null 2>&1 &
            fi
        fi
    fi
}

main "$@"
