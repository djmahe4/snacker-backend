#!/usr/bin/env python3
"""
=============================================================================
 SNACKER - Cloudflare Quick Tunnel Live Synchronizer
=============================================================================
 - Runs `cloudflared tunnel --url http://localhost:<NGINX_PORT>`
 - Parses the assigned `https://*.trycloudflare.com` URL
 - Publishes it to Firestore `config/gateway` (the Vercel frontend reads this)
 - Writes it to youpi-pg's .env as BASE_CHECKOUT_URL and restarts youpi-pg,
   so checkout links sent to phones use the public tunnel, not localhost
 - Exits when cloudflared exits; systemd restarts it and a new URL is synced
=============================================================================
"""

import os
import re
import signal
import subprocess
import sys
import tempfile

import firebase_admin
from firebase_admin import credentials, firestore

FIREBASE_KEY_PATH = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "serviceAccountKey.json"),
)
YOUPI_ENV_PATH = os.environ.get("YOUPI_ENV_PATH", "")
YOUPI_SERVICE = os.environ.get("YOUPI_SERVICE", "youpi-pg.service")
NGINX_PORT = os.environ.get("NGINX_PORT", "80")

URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def init_firestore():
    if not firebase_admin._apps:
        if os.path.exists(FIREBASE_KEY_PATH):
            firebase_admin.initialize_app(credentials.Certificate(FIREBASE_KEY_PATH))
        else:
            firebase_admin.initialize_app()
    return firestore.client()


def set_env_value(path, key, value):
    """Replace or append KEY=value in a dotenv file, atomically, keeping its permissions."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    mode = os.stat(path).st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def sync_youpi(tunnel_url):
    if not YOUPI_ENV_PATH or not os.path.exists(YOUPI_ENV_PATH):
        print(f"[YOUPI] YOUPI_ENV_PATH not set or missing ({YOUPI_ENV_PATH!r}); skipping checkout URL sync.")
        return
    try:
        set_env_value(YOUPI_ENV_PATH, "BASE_CHECKOUT_URL", tunnel_url)
        print(f"[YOUPI] BASE_CHECKOUT_URL set to {tunnel_url}")
    except OSError as e:
        print(f"[YOUPI] Could not update {YOUPI_ENV_PATH}: {e}")
        return
    # Allowed without a password by /etc/sudoers.d/snacker-tunnel (installed by setup_pi.sh)
    result = subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", "restart", YOUPI_SERVICE],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"[YOUPI] Restarted {YOUPI_SERVICE} to apply the new checkout URL.")
    else:
        print(f"[YOUPI] Restart failed ({result.stderr.strip()}). Run: sudo systemctl restart {YOUPI_SERVICE}")


def publish(db, data):
    try:
        db.collection("config").document("gateway").set(
            {**data, "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True
        )
    except Exception as e:
        print(f"[FIRESTORE] Write failed: {e}")


def in_maintenance(db):
    try:
        doc = db.collection("config").document("gateway").get()
        return doc.exists and (doc.to_dict() or {}).get("maintenance") is True
    except Exception as e:
        print(f"[FIRESTORE] Could not read maintenance flag: {e}")
        return False


def main():
    print("[QUICK-TUNNEL] Connecting to Firebase Firestore...")
    try:
        db = init_firestore()
    except Exception as e:
        print(f"[FATAL] Could not initialize Firebase: {e}")
        sys.exit(1)

    cmd = ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://localhost:{NGINX_PORT}"]
    print(f"[QUICK-TUNNEL] Launching: {' '.join(cmd)}")
    try:
        # cloudflared logs (including the URL) go to stderr; stdout is unused
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
    except FileNotFoundError:
        print("[FATAL] 'cloudflared' not found. Run: ./setup_pi.sh tunnel")
        sys.exit(1)

    # systemd stop -> SIGTERM: stop cloudflared and mark the gateway offline
    def shutdown(signum, frame):
        proc.terminate()
        publish(db, {"status": "offline"})
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    url_registered = False
    for line in proc.stderr:
        sys.stdout.write(line)
        sys.stdout.flush()
        match = URL_PATTERN.search(line)
        if match and not url_registered:
            tunnel_url = match.group(0)
            url_registered = True
            print(f"\n[DISCOVERED QUICK TUNNEL URL]: {tunnel_url}\n")
            # Admin maintenance mode keeps the gateway offline; the admin toggle restores
            # "online" when going live, using the hostUrl published here.
            publish(db, {
                "hostUrl": tunnel_url,
                "paymentApiUrl": f"{tunnel_url}/api/v1",
                "checkoutUrl": tunnel_url,
                "status": "offline" if in_maintenance(db) else "online",
            })
            print("[FIRESTORE] Live tunnel configuration written to 'config/gateway'.")
            sync_youpi(tunnel_url)

    code = proc.wait()
    publish(db, {"status": "offline"})
    print(f"[QUICK-TUNNEL] cloudflared exited ({code}); systemd will restart and resync.")
    sys.exit(1)


if __name__ == "__main__":
    main()
