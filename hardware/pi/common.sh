#!/bin/bash
# Shared helpers for setup_pi.sh and doctor.sh. Source, do not run.

PI_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load hardware/pi/pi.conf (falls back to pi.conf.example defaults)
load_pi_conf() {
    local conf="${PI_CONF:-$PI_SCRIPT_DIR/pi.conf}"
    if [ -f "$conf" ]; then
        # shellcheck disable=SC1090
        . "$conf"
        PI_CONF_LOADED="$conf"
    else
        # shellcheck disable=SC1091
        . "$PI_SCRIPT_DIR/pi.conf.example"
        PI_CONF_LOADED="$PI_SCRIPT_DIR/pi.conf.example (defaults; copy to pi.conf to customise)"
    fi
    if ! id "$PI_USER" >/dev/null 2>&1; then
        echo "❌ PI_USER '$PI_USER' does not exist on this system (from $PI_CONF_LOADED)." >&2
        echo "   Set PI_USER to your login user (see: whoami) in $PI_SCRIPT_DIR/pi.conf" >&2
        exit 1
    fi
}

# Read KEY from a dotenv file without sourcing it (never executes the file).
# Strips surrounding quotes and trailing inline comments.
env_get() {
    local file="$1" key="$2"
    [ -r "$file" ] || return 1
    grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1 | cut -d= -f2- \
        | sed -E 's/[[:space:]]+#.*$//; s/^["'\'']//; s/["'\'']$//'
}

# Short fingerprint of a secret so two values can be compared without printing them
fingerprint() {
    printf '%s' "$1" | sha256sum | cut -c1-8
}

# Render a systemd unit template with the configured user and paths
render_unit() {
    sed -e "s#^Environment=FIREBASE_SERVICE_ACCOUNT_PATH=.*#Environment=FIREBASE_SERVICE_ACCOUNT_PATH=${SERVICE_ACCOUNT_PATH}#"         -e "s#/home/pi/snacker-backend#${BACKEND_DIR}#g" \
        -e "s#/home/pi/snacker#${BACKEND_DIR}#g" \
        -e "s#/home/pi/youpi-pg#${YOUPI_DIR}#g" \
        -e "s#^User=pi\$#User=${PI_USER}#" \
        -e "s#^Group=pi\$#Group=${PI_USER}#" \
        -e "s#^Environment=PORT=5000\$#Environment=PORT=${BACKEND_PORT}#" \
        "$1"
}
