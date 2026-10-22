#!/bin/bash
# =============================================================================
#  SNACKER + YOUPI-PG UNIFIED RASPBERRY PI AUTOMATED PROVISIONING SCRIPT
# =============================================================================
#  Target OS: Raspberry Pi OS (64-bit Debian Bookworm / Bullseye)
#  Hardware: Raspberry Pi 3B+ / 4B / 5
#  Includes:
#    - System dependencies & Node.js 24 LTS
#    - Non-root USB HID scanner & Arduino Mega udev access
#    - Clone & SQLite automated provisioning of gxurav1710/youpi-pg
#    - Python 3 venv & evdev hardware vending daemon
#    - Systemd background services (vending controller + payment gateway)
#    - Nginx reverse proxy with unbuffered HMAC webhooks & SSE streams
# =============================================================================
set -e

echo "================================================================="
echo "  🍫 SNACKER & YOUPI-PG PROVISIONING STARTING ON RASPBERRY PI   "
echo "================================================================="

# -----------------------------------------------------------------------------
# 1. System Packages & Node.js 24 LTS
# -----------------------------------------------------------------------------
echo "📦 [1/6] Installing Linux system packages & build tools..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git nginx libudev-dev curl jq

# Install Node.js 24 LTS if not present or older than v20
NODE_MAJOR=0
if command -v node >/dev/null 2>&1; then
    NODE_MAJOR=$(node -v | cut -d'.' -f1 | tr -d 'v')
fi

if [ "$NODE_MAJOR" -lt 20 ]; then
    echo "⬇️  Installing Node.js 24 LTS from NodeSource (required for node:sqlite)..."
    curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
    sudo apt-get install -y nodejs
else
    echo "✅ Node.js $(node -v) is already installed."
fi

# -----------------------------------------------------------------------------
# 2. USB Permissions (QuickScan Barcode Scanner & Arduino Mega Serial)
# -----------------------------------------------------------------------------
echo "🔑 [2/6] Configuring udev rules and non-root hardware access..."
# Grant user 'pi' access to raw HID input devices (/dev/input/event*) and serial ports (/dev/ttyACM*)
sudo usermod -a -G input,dialout pi

# Create persistent udev rules for barcode scanner and serial microcontrollers
sudo bash -c 'cat <<EOF > /etc/udev/rules.d/99-snacker-scanner.rules
SUBSYSTEM=="input", ATTRS{name}=="*Barcode*", MODE="0660", GROUP="input"
SUBSYSTEM=="input", ATTRS{name}=="*Scanner*", MODE="0660", GROUP="input"
SUBSYSTEM=="input", ATTRS{name}=="*QuickScan*", MODE="0660", GROUP="input"
SUBSYSTEM=="input", ATTRS{name}=="*W106*", MODE="0660", GROUP="input"
KERNEL=="ttyACM[0-9]*", MODE="0660", GROUP="dialout"
KERNEL=="ttyUSB[0-9]*", MODE="0660", GROUP="dialout"
EOF'

sudo udevadm control --reload-rules
sudo udevadm trigger

# -----------------------------------------------------------------------------
# 3. Clone & Provision YouPI-PG Gateway (gxurav1710/youpi-pg)
# -----------------------------------------------------------------------------
YOUPI_DIR="/home/pi/youpi-pg"
echo "💳 [3/6] Setting up YouPI-PG Dynamic UPI Payment Gateway in $YOUPI_DIR..."

if [ ! -d "$YOUPI_DIR" ]; then
    echo "  Cloning gxurav1710/youpi-pg repository..."
    git clone https://github.com/gxurav1710/youpi-pg.git "$YOUPI_DIR"
else
    echo "  YouPI-PG directory already exists. Pulling latest updates..."
    cd "$YOUPI_DIR" && git pull || true
fi

cd "$YOUPI_DIR"
echo "  Installing YouPI-PG dependencies..."
npm install --omit=dev

# Generate production environment file if not present
if [ ! -f "$YOUPI_DIR/.env" ]; then
    echo "  Generating production credentials for YouPI-PG (SQLite WAL Mode)..."
    
    # Generate cryptographic master encryption keys
    MASTER_KEY=$(node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")
    JWT_KEY=$(node -e "console.log(require('crypto').randomBytes(24).toString('base64'))")
    WEBHOOK_SECRET=$(node -e "console.log(require('crypto').randomBytes(32).toString('hex'))")
    
    cat <<EOF > "$YOUPI_DIR/.env"
NODE_ENV=production
CONTROL_CENTER_PORT=8090
PAYMENT_API_PORT=6100
CHECKOUT_PORTAL_PORT=7000
MERCHANT_ADMIN_PORT=6001

# Zero-dependency embedded SQLite for ARM64 Raspberry Pi
USE_SQLITE_FALLBACK=true
SQLITE_DB_PATH=./data/youpi_pg.sqlite

# Security & Secrets
ENCRYPTION_MASTER_KEY=${MASTER_KEY}
JWT_SECRET=${JWT_KEY}

# Timers (5-min active slot, 30s verification)
PAYMENT_ACTIVE_WINDOW_MS=300000
PAYMENT_PROCESSING_WINDOW_MS=30000

# Merchant webhook dispatch
MERCHANT_WEBHOOK_URL=http://localhost:3000/api/youpi-webhook
MERCHANT_WEBHOOK_SECRET=${WEBHOOK_SECRET}

# URLs
BASE_PAYMENT_API_URL=http://localhost:6100
BASE_CHECKOUT_URL=http://localhost:7000
EOF

    # Initialize clean SQLite schema
    mkdir -p "$YOUPI_DIR/data"
    npm run db:setup || true
    echo "  ✅ YouPI-PG database initialized in SQLite mode."
    echo "  🔑 Webhook secret generated. Put the SAME value in the Snacker backend .env as YOUPI_WEBHOOK_SECRET:"
    echo "     $WEBHOOK_SECRET"
else
    echo "  ✅ YouPI-PG .env already configured."
fi

# Ensure user pi owns the folder
sudo chown -R pi:pi "$YOUPI_DIR"

# -----------------------------------------------------------------------------
# 4. Setup Python Virtual Environment for SNACKER Vending Daemon
# -----------------------------------------------------------------------------
echo "🐍 [4/6] Setting up Python virtual environment for Vending Controller..."
SNACKER_DIR="/home/pi/snacker"
VENV_DIR="$SNACKER_DIR/venv"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
if [ -f "$SNACKER_DIR/hardware/pi/requirements.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$SNACKER_DIR/hardware/pi/requirements.txt"
fi

# -----------------------------------------------------------------------------
# 5. Install & Enable Systemd Background Daemons
# -----------------------------------------------------------------------------
echo "⚙️ [5/6] Registering systemd background services..."

# A. YouPI-PG Payment Gateway Service
if [ -f "$SNACKER_DIR/hardware/pi/youpi-pg.service" ]; then
    sudo cp "$SNACKER_DIR/hardware/pi/youpi-pg.service" /etc/systemd/system/
fi

# B. Snacker Vending Hardware Controller
if [ -f "$SNACKER_DIR/hardware/pi/snacker-vending.service" ]; then
    sudo cp "$SNACKER_DIR/hardware/pi/snacker-vending.service" /etc/systemd/system/
fi

# C. Snacker Standalone Backend API (optional)
if [ -f "$SNACKER_DIR/hardware/pi/snacker-backend.service" ]; then
    sudo cp "$SNACKER_DIR/hardware/pi/snacker-backend.service" /etc/systemd/system/
fi

sudo systemctl daemon-reload
sudo systemctl enable youpi-pg.service snacker-vending.service
sudo systemctl restart youpi-pg.service snacker-vending.service

# -----------------------------------------------------------------------------
# 6. Configure Nginx Reverse Proxy
# -----------------------------------------------------------------------------
echo "🌐 [6/6] Configuring Nginx reverse proxy with SSE and webhook routes..."
if [ -f "$SNACKER_DIR/hardware/pi/nginx/snacker.conf" ]; then
    sudo cp "$SNACKER_DIR/hardware/pi/nginx/snacker.conf" /etc/nginx/sites-available/snacker
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo ln -sf /etc/nginx/sites-available/snacker /etc/nginx/sites-enabled/
    sudo nginx -t
    sudo systemctl reload nginx
fi

echo "================================================================="
echo "✨ SNACKER & YOUPI-PG PROVISIONING COMPLETED SUCCESSFULLY!       "
echo "================================================================="
echo "Active Services on Raspberry Pi:"
echo "  1. YouPI-PG Gateway      : http://localhost:7000 (Checkout Portal)"
echo "                           : http://localhost:6100 (Payment REST API)"
echo "                           : http://localhost:6001 (Merchant Admin)"
echo "  2. Vending Controller    : Fingers QuickScan + Arduino Mega Daemon"
echo "  3. Nginx Reverse Proxy   : Port 80 -> UI & Gateway endpoints"
echo ""
echo "Monitoring Commands:"
echo "  View Vending Scanner Logs: sudo journalctl -u snacker-vending.service -f"
echo "  View YouPI Gateway Logs  : sudo journalctl -u youpi-pg.service -f"
echo "  Check Service Status     : sudo systemctl status youpi-pg.service snacker-vending.service"
echo "================================================================="
