#!/bin/bash
# =============================================================================
#  SNACKER + YOUPI-PG RASPBERRY PI PROVISIONING
# =============================================================================
#  Target OS: Raspberry Pi OS (64-bit Debian Bookworm / Bullseye)
#  Frontend:  Vercel only. Nothing on the Pi serves the PWA.
#
#  Paths, user and ports come from hardware/pi/pi.conf (see pi.conf.example).
#  Every step can run on its own, so you can learn, debug and re-run one piece:
#
#    ./setup_pi.sh --list              # show steps
#    ./setup_pi.sh packages            # run one step
#    ./setup_pi.sh youpi env services  # run several steps in order
#    ./setup_pi.sh --dry-run services  # print commands without running them
#    ./setup_pi.sh --all               # run every step
#
#  After any step, run ./doctor.sh to check the result (read-only).
# =============================================================================
set -euo pipefail

# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_pi_conf

DRY_RUN=0
STEPS_ALL=(packages udev youpi venv env services nginx)

# Print a command, then run it (or only print it with --dry-run)
run() {
    echo "  \$ $*"
    [ "$DRY_RUN" -eq 1 ] || "$@"
}

usage() {
    cat <<EOF
Usage: $0 [--dry-run] (--all | --list | STEP...)

Steps:
  packages  apt packages and Node.js 24
  udev      scanner/serial udev rules, add $PI_USER to input,dialout
  youpi     clone/update youpi-pg, npm install, first-time .env + SQLite
  venv      Python venv + requirements for the vending daemon
  env       create backend .env (mode 600) and sync the webhook secret from youpi-pg
  services  install systemd units rendered with pi.conf paths, enable + restart
  nginx     install hardware/pi/nginx/$NGINX_SITE.conf and reload nginx

Config: $PI_CONF_LOADED
EOF
}

step_packages() {
    echo "📦 Installing system packages..."
    run sudo apt-get update
    run sudo apt-get install -y python3 python3-pip python3-venv git nginx libudev-dev curl jq

    local node_major=0
    if command -v node >/dev/null 2>&1; then
        node_major=$(node -v | cut -d'.' -f1 | tr -d 'v')
    fi
    if [ "$node_major" -lt 20 ]; then
        echo "⬇️  Installing Node.js 24 LTS (required for node:sqlite)..."
        echo "  \$ curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -"
        [ "$DRY_RUN" -eq 1 ] || curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
        run sudo apt-get install -y nodejs
    else
        echo "  ✅ Node.js $(node -v) already installed."
    fi
}

step_udev() {
    echo "🔑 Configuring udev rules and hardware access for $PI_USER..."
    run sudo usermod -a -G input,dialout "$PI_USER"
    local rules='SUBSYSTEM=="input", ATTRS{name}=="*Barcode*", MODE="0660", GROUP="input"
SUBSYSTEM=="input", ATTRS{name}=="*Scanner*", MODE="0660", GROUP="input"
SUBSYSTEM=="input", ATTRS{name}=="*QuickScan*", MODE="0660", GROUP="input"
SUBSYSTEM=="input", ATTRS{name}=="*W106*", MODE="0660", GROUP="input"
KERNEL=="ttyACM[0-9]*", MODE="0660", GROUP="dialout"
KERNEL=="ttyUSB[0-9]*", MODE="0660", GROUP="dialout"'
    echo "  \$ (write) /etc/udev/rules.d/99-snacker-scanner.rules"
    [ "$DRY_RUN" -eq 1 ] || printf '%s\n' "$rules" | sudo tee /etc/udev/rules.d/99-snacker-scanner.rules >/dev/null
    run sudo udevadm control --reload-rules
    run sudo udevadm trigger
    echo "  ℹ️  Log out and back in for the new groups to apply to $PI_USER."
}

step_youpi() {
    echo "💳 Setting up YouPI-PG in $YOUPI_DIR..."
    if [ ! -d "$YOUPI_DIR" ]; then
        run git clone https://github.com/gxurav1710/youpi-pg.git "$YOUPI_DIR"
    else
        run git -C "$YOUPI_DIR" pull --ff-only
    fi
    run npm --prefix "$YOUPI_DIR" install --omit=dev

    if [ -f "$YOUPI_DIR/.env" ]; then
        echo "  ✅ $YOUPI_DIR/.env already exists; not touching it."
    else
        echo "  Generating $YOUPI_DIR/.env with fresh keys (values are not printed)..."
        if [ "$DRY_RUN" -eq 0 ]; then
            local master jwt webhook
            master=$(openssl rand -hex 32)
            jwt=$(openssl rand -base64 24)
            webhook=$(openssl rand -hex 32)
            ( umask 077; cat > "$YOUPI_DIR/.env" <<EOF
NODE_ENV=production
CONTROL_CENTER_PORT=8090
PAYMENT_API_PORT=${YOUPI_API_PORT}
CHECKOUT_PORTAL_PORT=${YOUPI_CHECKOUT_PORT}
MERCHANT_ADMIN_PORT=${YOUPI_ADMIN_PORT}

# Zero-dependency embedded SQLite for ARM64 Raspberry Pi
USE_SQLITE_FALLBACK=true
SQLITE_DB_PATH=./data/youpi_pg.sqlite

# Security & Secrets
ENCRYPTION_MASTER_KEY=${master}
JWT_SECRET=${jwt}

# Timers (5-min active slot, 30s verification)
PAYMENT_ACTIVE_WINDOW_MS=300000
PAYMENT_PROCESSING_WINDOW_MS=30000

# Merchant webhook dispatch (Express backend on the Pi)
MERCHANT_WEBHOOK_URL=http://localhost:${BACKEND_PORT}/api/youpi-webhook
MERCHANT_WEBHOOK_SECRET=${webhook}

# URLs
BASE_PAYMENT_API_URL=http://localhost:${YOUPI_API_PORT}
BASE_CHECKOUT_URL=http://localhost:${YOUPI_CHECKOUT_PORT}
EOF
            )
        fi
        run mkdir -p "$YOUPI_DIR/data"
        echo "  \$ (cd $YOUPI_DIR && npm run db:setup)"
        [ "$DRY_RUN" -eq 1 ] || (cd "$YOUPI_DIR" && npm run db:setup) || true
        echo "  ℹ️  Run './setup_pi.sh env' to copy the webhook secret into the backend .env."
    fi
    run sudo chown -R "$PI_USER:$PI_USER" "$YOUPI_DIR"
}

step_venv() {
    echo "🐍 Setting up Python venv in $BACKEND_DIR/venv..."
    [ -d "$BACKEND_DIR/venv" ] || run python3 -m venv "$BACKEND_DIR/venv"
    run "$BACKEND_DIR/venv/bin/pip" install --upgrade pip
    run "$BACKEND_DIR/venv/bin/pip" install -r "$BACKEND_DIR/hardware/pi/requirements.txt"
}

step_env() {
    local env_file="$BACKEND_DIR/.env"
    echo "🔐 Preparing $env_file..."
    if [ ! -f "$env_file" ]; then
        run cp "$BACKEND_DIR/.env.example" "$env_file"
        echo "  ✏️  Edit $env_file: set ADMIN_EMAIL, CLERK_SECRET_KEY, Firebase values."
    fi
    run chmod 600 "$env_file"
    [ -f "$YOUPI_DIR/.env" ] && run chmod 600 "$YOUPI_DIR/.env"
    [ -f "$SERVICE_ACCOUNT_PATH" ] && run chmod 600 "$SERVICE_ACCOUNT_PATH"

    local secret
    secret=$(env_get "$YOUPI_DIR/.env" MERCHANT_WEBHOOK_SECRET || true)
    if [ -z "$secret" ]; then
        echo "  ⚠️  No MERCHANT_WEBHOOK_SECRET in $YOUPI_DIR/.env; run the 'youpi' step first."
        return 0
    fi
    if [ "$(env_get "$env_file" YOUPI_WEBHOOK_SECRET || true)" = "$secret" ]; then
        echo "  ✅ YOUPI_WEBHOOK_SECRET already matches youpi-pg (fingerprint $(fingerprint "$secret"))."
        return 0
    fi
    echo "  \$ (set) YOUPI_WEBHOOK_SECRET in $env_file from youpi-pg (fingerprint $(fingerprint "$secret"))"
    if [ "$DRY_RUN" -eq 0 ]; then
        if grep -qE '^YOUPI_WEBHOOK_SECRET=' "$env_file"; then
            # Bash builtins only, so the secret never appears in a process argument list
            local tmp line
            tmp=$(mktemp)
            while IFS= read -r line || [ -n "$line" ]; do
                case "$line" in
                    YOUPI_WEBHOOK_SECRET=*) printf 'YOUPI_WEBHOOK_SECRET=%s\n' "$secret" ;;
                    *) printf '%s\n' "$line" ;;
                esac
            done < "$env_file" > "$tmp"
            cat "$tmp" > "$env_file"
            rm -f "$tmp"
        else
            printf 'YOUPI_WEBHOOK_SECRET=%s\n' "$secret" >> "$env_file"
        fi
    fi
    echo "  ℹ️  Also set the same value in Vercel (YOUPI_WEBHOOK_SECRET), then redeploy."
}

install_unit() {
    local name="$1"
    echo "  \$ (render $name with pi.conf) > /etc/systemd/system/$name"
    if [ "$DRY_RUN" -eq 1 ]; then
        render_unit "$PI_SCRIPT_DIR/$name" | grep -E '^(User|Group|WorkingDirectory|ExecStart|Environment=PORT)=' | sed 's/^/      /'
    else
        render_unit "$PI_SCRIPT_DIR/$name" | sudo tee "/etc/systemd/system/$name" >/dev/null
    fi
}

step_services() {
    echo "⚙️  Installing systemd units..."
    install_unit youpi-pg.service
    install_unit snacker-vending.service
    install_unit snacker-backend.service
    run sudo systemctl daemon-reload
    run sudo systemctl enable youpi-pg.service snacker-vending.service snacker-backend.service
    run sudo systemctl restart youpi-pg.service snacker-vending.service snacker-backend.service
}

step_nginx() {
    local src="$PI_SCRIPT_DIR/nginx/$NGINX_SITE.conf"
    echo "🌐 Installing nginx site '$NGINX_SITE'..."
    [ -f "$src" ] || { echo "  ❌ $src not found"; return 1; }
    run sudo cp "$src" "/etc/nginx/sites-available/$NGINX_SITE"
    run sudo rm -f /etc/nginx/sites-enabled/default
    run sudo ln -sf "/etc/nginx/sites-available/$NGINX_SITE" "/etc/nginx/sites-enabled/$NGINX_SITE"
    run sudo nginx -t
    run sudo systemctl reload nginx
}

# ---- argument parsing -------------------------------------------------------
STEPS=()
[ $# -eq 0 ] && { usage; exit 0; }
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --all) STEPS=("${STEPS_ALL[@]}") ;;
        --list|-h|--help) usage; exit 0 ;;
        packages|udev|youpi|venv|env|services|nginx) STEPS+=("$1") ;;
        *) echo "Unknown step: $1"; usage; exit 1 ;;
    esac
    shift
done

echo "Config: $PI_CONF_LOADED"
[ "$DRY_RUN" -eq 1 ] && echo "Dry run: commands are printed, not executed."
for s in "${STEPS[@]}"; do
    echo
    "step_$s"
done
echo
echo "Done. Check the result with: $PI_SCRIPT_DIR/doctor.sh"
