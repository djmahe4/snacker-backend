#!/bin/bash
# =============================================================================
#  SNACKER PI DOCTOR (read-only)
# =============================================================================
#  Checks env files, file permissions, secrets, systemd units, ports and nginx
#  against hardware/pi/pi.conf. It never changes anything and never prints a
#  secret value: secrets are shown only as length + sha256 fingerprint.
#
#    ./doctor.sh            # all checks
#    ./doctor.sh env ports  # only some sections (env files ports systemd nginx http)
# =============================================================================
set -uo pipefail

# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_pi_conf

PASS=0 WARN=0 FAIL=0
ok()   { echo "  ✅ $*"; PASS=$((PASS + 1)); }
warn() { echo "  ⚠️  $*"; WARN=$((WARN + 1)); }
bad()  { echo "  ❌ $*"; FAIL=$((FAIL + 1)); }
hint() { echo "     ↳ $*"; }

LEAKED_SECRET="snacker_youpi_secret_8899"
BACKEND_ENV="$BACKEND_DIR/.env"
YOUPI_ENV="$YOUPI_DIR/.env"

describe_secret() {
    printf 'len=%s fp=%s' "${#1}" "$(fingerprint "$1")"
}

# --- file ownership / mode ---------------------------------------------------
check_private_file() {
    local f="$1" label="$2"
    if [ ! -e "$f" ]; then
        bad "$label missing: $f"
        return 1
    fi
    local mode owner
    mode=$(stat -c '%a' "$f")
    owner=$(stat -c '%U' "$f")
    if [ "$mode" = "600" ] || [ "$mode" = "400" ]; then
        ok "$label mode $mode, owner $owner"
    else
        warn "$label is mode $mode (readable by others)"
        hint "chmod 600 $f"
    fi
    [ "$owner" = "$PI_USER" ] || { warn "$label owned by $owner, services run as $PI_USER"; hint "sudo chown $PI_USER:$PI_USER $f"; }
    return 0
}

# --- required key present and not a placeholder ------------------------------
check_key() {
    local file="$1" key="$2" secret="${3:-}"
    local v
    v=$(env_get "$file" "$key" || true)
    if [ -z "$v" ]; then
        bad "$key is not set in $file"
        return
    fi
    case "$v" in
        *change-me*|*your_*|*you@example.com*|*Dummy*|*example*)
            bad "$key in $file still has the placeholder value"
            return ;;
    esac
    if [ "$v" = "$LEAKED_SECRET" ]; then
        bad "$key in $file is the leaked public secret; rotate it (see README)"
        return
    fi
    if [ -n "$secret" ]; then
        ok "$key set ($(describe_secret "$v"))"
    else
        ok "$key = $v"
    fi
}

section_config() {
    echo "== Config"
    echo "  Using: $PI_CONF_LOADED"
    echo "  user=$PI_USER backend=$BACKEND_DIR youpi=$YOUPI_DIR nginx=$NGINX_SITE"
    echo "  ports: backend=$BACKEND_PORT api=$YOUPI_API_PORT checkout=$YOUPI_CHECKOUT_PORT admin=$YOUPI_ADMIN_PORT nginx=$NGINX_PORT"
    id "$PI_USER" >/dev/null 2>&1 && ok "user $PI_USER exists" || bad "user $PI_USER does not exist"
    [ -d "$BACKEND_DIR" ] && ok "backend dir exists" || bad "backend dir missing: $BACKEND_DIR"
    [ -d "$YOUPI_DIR" ] && ok "youpi-pg dir exists" || bad "youpi-pg dir missing: $YOUPI_DIR"
    if command -v node >/dev/null 2>&1; then
        local major
        major=$(node -v | cut -d. -f1 | tr -d v)
        [ "$major" -ge 20 ] && ok "node $(node -v)" || bad "node $(node -v) is older than 20"
    else
        bad "node not installed"
    fi
}

section_files() {
    echo "== Secret files"
    check_private_file "$BACKEND_ENV" "backend .env"
    check_private_file "$YOUPI_ENV" "youpi-pg .env"
    check_private_file "$SERVICE_ACCOUNT_PATH" "service account JSON"
    if [ -d "$BACKEND_DIR/.git" ]; then
        local tracked
        tracked=$(git -C "$BACKEND_DIR" ls-files .env serviceAccountKey.json hardware/pi/pi.conf 2>/dev/null)
        [ -z "$tracked" ] && ok "no secret files tracked by git" || bad "tracked by git: $tracked"
    fi
}

section_env() {
    echo "== Backend env ($BACKEND_ENV)"
    if [ -r "$BACKEND_ENV" ]; then
        check_key "$BACKEND_ENV" YOUPI_WEBHOOK_SECRET secret
        check_key "$BACKEND_ENV" CLERK_SECRET_KEY secret
        check_key "$BACKEND_ENV" ADMIN_EMAIL secret
        local port
        port=$(env_get "$BACKEND_ENV" PORT || true)
        if [ -n "$port" ] && [ "$port" != "$BACKEND_PORT" ]; then
            warn "PORT=$port in .env but pi.conf BACKEND_PORT=$BACKEND_PORT (systemd Environment=PORT wins)"
        fi
        if grep -qE '^NEXT_PUBLIC_ADMIN_EMAIL=' "$BACKEND_ENV"; then
            bad "NEXT_PUBLIC_ADMIN_EMAIL found: admin email must be server-only"
        fi
        local sa
        sa=$(env_get "$BACKEND_ENV" FIREBASE_SERVICE_ACCOUNT_PATH || true)
        if [ -n "$sa" ] && [ "$sa" != "$SERVICE_ACCOUNT_PATH" ]; then
            warn "FIREBASE_SERVICE_ACCOUNT_PATH=$sa differs from pi.conf SERVICE_ACCOUNT_PATH"
        fi
        [ -n "$sa" ] && [ ! -f "$sa" ] && bad "FIREBASE_SERVICE_ACCOUNT_PATH points to a missing file: $sa"
    else
        bad "cannot read $BACKEND_ENV"
    fi

    echo "== YouPI-PG env ($YOUPI_ENV)"
    if [ -r "$YOUPI_ENV" ]; then
        check_key "$YOUPI_ENV" MERCHANT_WEBHOOK_SECRET secret
        check_key "$YOUPI_ENV" ENCRYPTION_MASTER_KEY secret
        check_key "$YOUPI_ENV" JWT_SECRET secret
        local url want
        url=$(env_get "$YOUPI_ENV" MERCHANT_WEBHOOK_URL || true)
        want="http://localhost:${BACKEND_PORT}/api/youpi-webhook"
        if [ "$url" = "$want" ] || [ "$url" = "http://127.0.0.1:${BACKEND_PORT}/api/youpi-webhook" ]; then
            ok "MERCHANT_WEBHOOK_URL = $url"
        else
            bad "MERCHANT_WEBHOOK_URL = ${url:-<unset>}"
            hint "expected $want (frontend is on Vercel; the Pi backend receives webhooks)"
        fi
    else
        bad "cannot read $YOUPI_ENV"
    fi

    echo "== Webhook secret match"
    local a b
    a=$(env_get "$BACKEND_ENV" YOUPI_WEBHOOK_SECRET || true)
    b=$(env_get "$YOUPI_ENV" MERCHANT_WEBHOOK_SECRET || true)
    if [ -n "$a" ] && [ "$a" = "$b" ]; then
        ok "backend YOUPI_WEBHOOK_SECRET == youpi MERCHANT_WEBHOOK_SECRET (fp $(fingerprint "$a"))"
        hint "Vercel YOUPI_WEBHOOK_SECRET should have the same fingerprint:"
        hint "printf '%s' \"\$VALUE\" | sha256sum | cut -c1-8"
    else
        bad "webhook secrets differ (backend fp $(fingerprint "$a"), youpi fp $(fingerprint "$b"))"
        hint "./setup_pi.sh env   # copies the youpi-pg value into the backend .env"
    fi
}

section_systemd() {
    echo "== systemd"
    local unit wd user
    for unit in snacker-backend youpi-pg snacker-vending; do
        local path="/etc/systemd/system/$unit.service"
        if [ ! -f "$path" ]; then
            bad "$unit.service not installed"
            continue
        fi
        wd=$(grep -E '^WorkingDirectory=' "$path" | cut -d= -f2-)
        user=$(grep -E '^User=' "$path" | cut -d= -f2-)
        [ -d "$wd" ] || bad "$unit WorkingDirectory does not exist: $wd"
        [ "$user" = "$PI_USER" ] || warn "$unit runs as User=$user, pi.conf says $PI_USER"
        if [ "$unit" = snacker-backend ] && [ "$wd" != "$BACKEND_DIR" ]; then
            warn "$unit WorkingDirectory=$wd, pi.conf BACKEND_DIR=$BACKEND_DIR (dotenv reads .env from WorkingDirectory)"
        fi
        if systemctl is-active --quiet "$unit"; then
            ok "$unit active (dir $wd, user $user)"
        else
            bad "$unit is $(systemctl is-active "$unit" 2>/dev/null)"
            hint "journalctl -u $unit -n 50 --no-pager"
        fi
    done
}

section_ports() {
    echo "== Listening ports"
    local name port
    for pair in "backend:$BACKEND_PORT" "youpi-api:$YOUPI_API_PORT" "youpi-checkout:$YOUPI_CHECKOUT_PORT" "youpi-admin:$YOUPI_ADMIN_PORT" "nginx:$NGINX_PORT"; do
        name=${pair%%:*}
        port=${pair##*:}
        if ss -ltnH "sport = :$port" 2>/dev/null | grep -q .; then
            ok "$name listening on :$port"
        else
            bad "$name not listening on :$port"
        fi
    done
    if ss -ltnH "sport = :3000" 2>/dev/null | grep -q .; then
        warn "something listens on :3000; the frontend is on Vercel, so nothing should"
    fi
}

section_nginx() {
    echo "== nginx ($NGINX_SITE)"
    local avail="/etc/nginx/sites-available/$NGINX_SITE"
    local enabled="/etc/nginx/sites-enabled/$NGINX_SITE"
    local repo="$PI_SCRIPT_DIR/nginx/$NGINX_SITE.conf"
    [ -f "$avail" ] && ok "$avail exists" || bad "$avail missing"
    [ -L "$enabled" ] && ok "$NGINX_SITE enabled" || bad "$NGINX_SITE not enabled ($enabled)"
    [ -e /etc/nginx/sites-enabled/default ] && warn "default site still enabled; it may shadow $NGINX_SITE"
    local others
    others=$(ls /etc/nginx/sites-enabled 2>/dev/null | grep -vx "$NGINX_SITE" | grep -vx default)
    [ -n "$others" ] && warn "other enabled sites: $others"
    if [ -f "$avail" ]; then
        local target
        target=$(awk '/location[[:space:]]+\/api\/youpi-webhook/{f=1} f&&/proxy_pass/{print $2; exit}' "$avail" | tr -d ';')
        if [ "$target" = "http://127.0.0.1:${BACKEND_PORT}/api/youpi-webhook" ]; then
            ok "/api/youpi-webhook -> $target"
        else
            bad "/api/youpi-webhook -> ${target:-<none>}, expected :$BACKEND_PORT"
        fi
        grep -q 'proxy_request_buffering off' "$avail" && ok "webhook request buffering off (raw body kept for HMAC)" \
            || warn "proxy_request_buffering off not found; HMAC may fail on large bodies"
        if [ -f "$repo" ] && ! diff -q "$repo" "$avail" >/dev/null; then
            warn "installed site differs from repo $repo"
            hint "diff $repo $avail"
        fi
    fi
    if sudo -n true 2>/dev/null; then
        sudo nginx -t >/dev/null 2>&1 && ok "nginx -t passes" || bad "nginx -t fails (run: sudo nginx -t)"
    else
        echo "  ℹ️  skipped 'nginx -t' (needs sudo; run: sudo nginx -t)"
    fi
}

section_http() {
    echo "== HTTP"
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${BACKEND_PORT}/health")
    [ "$code" = 200 ] && ok "backend /health 200" || bad "backend /health returned $code"
    # Unsigned request through nginx: must reach Express and be rejected, not 502
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -X POST \
        -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:${NGINX_PORT}/api/youpi-webhook")
    case "$code" in
        200|400|401) ok "nginx /api/youpi-webhook reaches backend (unsigned test got $code)" ;;
        502|504) bad "nginx /api/youpi-webhook returned $code: upstream on :$BACKEND_PORT is down or wrong" ;;
        *) warn "nginx /api/youpi-webhook returned $code" ;;
    esac
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${NGINX_PORT}/youpi.js")
    [ "$code" = 200 ] && ok "nginx /youpi.js 200" || warn "nginx /youpi.js returned $code"
}

SECTIONS=("$@")
[ ${#SECTIONS[@]} -eq 0 ] && SECTIONS=(files env systemd ports nginx http)
section_config
for s in "${SECTIONS[@]}"; do
    case "$s" in
        files|env|systemd|ports|nginx|http) echo; "section_$s" ;;
        *) echo "Unknown section: $s (files env systemd ports nginx http)"; exit 2 ;;
    esac
done

echo
echo "Summary: $PASS ok, $WARN warnings, $FAIL failures"
[ "$FAIL" -eq 0 ]
