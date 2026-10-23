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
STEPS_ALL=(packages udev youpi backend venv env services nginx tunnel upi merchant observer)

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
  backend   npm install for this repo's Express API (snacker-backend.service)
  venv      Python venv + requirements for the vending daemon
  env       create backend .env (mode 600) and sync the webhook secret from youpi-pg
  services  install systemd units rendered with pi.conf paths, enable + restart
  nginx     install hardware/pi/nginx/$NGINX_SITE.conf and reload nginx
  tunnel    cloudflared + sync-quick-tunnel.service: publishes the tunnel URL to
            Firestore and youpi-pg BASE_CHECKOUT_URL on every start
  upi       set the UPI ID + payee name customers pay to (REQUIRED: default is a demo
            placeholder). UPI_ID=... PAYEE_NAME=... or answer the prompts
  merchant  point youpi-pg's payment webhook at the backend with the backend's secret
            (stored in youpi's DB, not .env); prints the merchant ID for Vercel
  observer  REQUIRED for payments to confirm: prints the APK build command (gateway
            discovery for this Firebase project), the live tunnel URL and a fresh
            10-minute enrollment token for pairing the Android observer app

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
            local master jwt webhook admin_token
            master=$(openssl rand -hex 32)
            jwt=$(openssl rand -base64 24)
            webhook=$(openssl rand -hex 32)
            admin_token=$(openssl rand -hex 32)
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
# Bearer token for the youpi-pg admin API (used by './setup_pi.sh upi' and doctor)
ADMIN_API_TOKEN=${admin_token}

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
        echo "  ⚠️  The receiving UPI ID is a demo placeholder until you run './setup_pi.sh upi'."
    fi
    run sudo chown -R "$PI_USER:$PI_USER" "$YOUPI_DIR"
}

step_backend() {
    echo "🟢 Installing backend Node dependencies in $BACKEND_DIR..."
    if [ -f "$BACKEND_DIR/package-lock.json" ]; then
        run npm --prefix "$BACKEND_DIR" ci --omit=dev
    else
        run npm --prefix "$BACKEND_DIR" install --omit=dev
    fi
    run sudo chown -R "$PI_USER:$PI_USER" "$BACKEND_DIR/node_modules"
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

step_tunnel() {
    echo "☁️  Setting up the Cloudflare Quick Tunnel sync..."
    if command -v cloudflared >/dev/null 2>&1; then
        echo "  ✅ $(cloudflared --version 2>&1 | head -n 1)"
    else
        local arch deb
        arch=$(dpkg --print-architecture)
        deb=$(mktemp --suffix=.deb)
        run curl -fsSL -o "$deb" "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
        run sudo dpkg -i "$deb"
        rm -f "$deb"
    fi

    # Let the tunnel service restart youpi-pg (to apply a new checkout URL) and nothing else
    local rule="$PI_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart youpi-pg.service"
    echo "  \$ (write) /etc/sudoers.d/snacker-tunnel: $rule"
    if [ "$DRY_RUN" -eq 0 ]; then
        local tmp
        tmp=$(mktemp)
        printf '%s\n' "$rule" > "$tmp"
        if sudo visudo -cf "$tmp" >/dev/null; then
            sudo install -m 0440 -o root -g root "$tmp" /etc/sudoers.d/snacker-tunnel
        else
            echo "  ❌ sudoers rule failed validation; not installed"
        fi
        rm -f "$tmp"
    fi

    install_unit sync-quick-tunnel.service
    run sudo systemctl daemon-reload
    run sudo systemctl enable sync-quick-tunnel.service
    run sudo systemctl restart sync-quick-tunnel.service
    echo "  ℹ️  Watch for the URL: journalctl -u sync-quick-tunnel -f"
}

step_upi() {
    # Usage: UPI_ID=name@bank PAYEE_NAME="NAME AS IN BANK" ./setup_pi.sh upi   (or answer the prompts)
    local env_file="$YOUPI_DIR/.env"
    echo "🏦 Setting the UPI ID that customers pay to..."
    [ -f "$env_file" ] || { echo "  ❌ $env_file missing; run the 'youpi' step first"; return 1; }

    if [ -z "$(env_get "$env_file" ADMIN_API_TOKEN || true)" ]; then
        echo "  \$ (append) ADMIN_API_TOKEN to $env_file, then restart youpi-pg"
        if [ "$DRY_RUN" -eq 0 ]; then
            printf 'ADMIN_API_TOKEN=%s\n' "$(openssl rand -hex 32)" >> "$env_file"
            sudo systemctl restart youpi-pg.service
            sleep 5
        fi
    fi

    local upi="${UPI_ID:-}" payee="${PAYEE_NAME:-}"
    [ -n "$upi" ] || read -r -p "  UPI ID (e.g. 9876543210@ybl): " upi
    [ -n "$payee" ] || read -r -p "  Payee name exactly as your bank shows it: " payee
    [ -n "$upi" ] && [ -n "$payee" ] || { echo "  ❌ UPI ID and payee name are both required"; return 1; }

    echo "  \$ POST /api/v1/admin/upi-settings  upiId=$upi payeeName=$payee"
    [ "$DRY_RUN" -eq 1 ] && return 0
    local body token
    token=$(env_get "$env_file" ADMIN_API_TOKEN)
    body=$(jq -n --arg u "$upi" --arg p "$payee" '{upiId: $u, payeeName: $p}')
    # Token passed on stdin as a header so it never shows up in `ps`
    printf 'Authorization: Bearer %s\n' "$token" | curl -s --max-time 10 -H @- \
        -H 'Content-Type: application/json' -d "$body" \
        "http://127.0.0.1:${YOUPI_API_PORT}/api/v1/admin/upi-settings" | jq .
    echo "  ℹ️  Verify with: ./doctor.sh upi"
}

# Call the youpi-pg admin API. Token goes via stdin as a header so it never shows in `ps`.
youpi_admin() {
    local method="$1" path="$2" body="${3:-}" token
    token=$(env_get "$YOUPI_DIR/.env" ADMIN_API_TOKEN || true)
    [ -n "$token" ] || { echo "  ❌ no ADMIN_API_TOKEN in $YOUPI_DIR/.env; run './setup_pi.sh upi' first" >&2; return 1; }
    if [ -n "$body" ]; then
        printf 'Authorization: Bearer %s\n' "$token" | curl -s --max-time 10 -X "$method" -H @- \
            -H 'Content-Type: application/json' -d "$body" "http://127.0.0.1:${YOUPI_API_PORT}${path}"
    else
        printf 'Authorization: Bearer %s\n' "$token" | curl -s --max-time 10 -X "$method" -H @- \
            "http://127.0.0.1:${YOUPI_API_PORT}${path}"
    fi
}

step_merchant() {
    # youpi-pg delivers payment webhooks using the merchant record in its DB, NOT .env.
    # Its seed points at http://localhost:5000/api/webhook with a random secret, so wallet
    # credits would never land. Point it at our backend with the backend's secret.
    local url="http://127.0.0.1:${BACKEND_PORT}/api/youpi-webhook" secret body mid
    echo "🔗 Pointing youpi-pg payment webhooks at the Snacker backend..."
    secret=$(env_get "$BACKEND_DIR/.env" YOUPI_WEBHOOK_SECRET || true)
    [ -n "$secret" ] || { echo "  ❌ YOUPI_WEBHOOK_SECRET missing in $BACKEND_DIR/.env; run the 'env' step first"; return 1; }
    echo "  \$ POST /api/v1/admin/merchant-settings webhookUrl=$url webhookSecret=(backend secret, fingerprint $(fingerprint "$secret"))"
    [ "$DRY_RUN" -eq 1 ] && return 0
    body=$(jq -n --arg u "$url" --arg s "$secret" '{webhookUrl: $u, webhookSecret: $s}')
    youpi_admin POST /api/v1/admin/merchant-settings "$body" | jq '{merchantId, webhookUrl, error}'
    mid=$(youpi_admin GET /api/v1/admin/merchant-settings | jq -r .merchantId)
    echo "  ℹ️  In Vercel set YOUPI_MERCHANT_ID and NEXT_PUBLIC_YOUPI_MERCHANT_ID to: $mid"
    echo "     (any other value makes /api/v1/payments answer 400 'Merchant not found or not configured')"
}

step_observer() {
    # youpi-pg confirms a payment only when an observer reports the bank credit alert.
    # This step does not touch the phone; it prints what the phone needs.
    echo "📱 Android payment observer (pairing info)..."
    local project="" tunnel="" pair
    if [ -r "$SERVICE_ACCOUNT_PATH" ]; then
        project=$(jq -r '.project_id // empty' "$SERVICE_ACCOUNT_PATH" 2>/dev/null || true)
    fi
    tunnel=$(journalctl -u sync-quick-tunnel -n 300 --no-pager 2>/dev/null \
        | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -n 1 || true)

    echo "  1. Build the observer APK (fork djmahe4/YouPI-PG, branch feat/observer-gateway-discovery):"
    if [ -n "$project" ]; then
        echo "     ./gradlew assembleDebug -PgatewayDiscoveryUrl='https://firestore.googleapis.com/v1/projects/${project}/databases/(default)/documents/config/gateway'"
    else
        echo "     ⚠️  project_id not readable from $SERVICE_ACCOUNT_PATH; use your Firebase project ID in -PgatewayDiscoveryUrl"
    fi
    echo "     Firestore rules must allow public read of config/gateway (firebase/firestore.rules)."
    echo "  2. On the phone: install, grant Notification Listener access, battery = Unrestricted."
    if [ -n "$tunnel" ]; then
        echo "  3. Gateway URL to TYPE in the app (do not scan the admin QR): $tunnel"
    else
        echo "  3. ⚠️  No tunnel URL in the sync-quick-tunnel log; run './setup_pi.sh tunnel' first."
    fi

    [ "$DRY_RUN" -eq 1 ] && { echo "  \$ GET /api/v1/admin/sms-gateway/pair-qr (enrollment token)"; return 0; }
    pair=$(youpi_admin GET /api/v1/admin/sms-gateway/pair-qr) || return 1
    # The token is a pairing credential: printed once for you to type, never logged elsewhere.
    echo "  4. Enrollment token (valid 10 minutes): $(printf '%s' "$pair" | jq -r '.enrollmentToken // .token // "unavailable"')"
    echo "  5. Tap Pair, then verify with: ./doctor.sh upi   (expect 'payment observer connected')"
}

# ---- argument parsing -------------------------------------------------------
STEPS=()
[ $# -eq 0 ] && { usage; exit 0; }
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --all) STEPS=("${STEPS_ALL[@]}") ;;
        --list|-h|--help) usage; exit 0 ;;
        packages|udev|youpi|backend|venv|env|services|nginx|tunnel|upi|merchant|observer) STEPS+=("$1") ;;
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
