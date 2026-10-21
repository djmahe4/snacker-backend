#!/usr/bin/env python3
"""
=============================================================================
 SNACKER - Cloudflare Quick Tunnel Live Synchronizer
=============================================================================
 - Runs `cloudflared tunnel --url http://localhost:80`
 - Parses the assigned `https://*.trycloudflare.com` URL
 - Publishes live tunnel coordinates to Firestore collection `config/gateway`
 - Eliminates manual Vercel redeploys when testing without a custom domain!
=============================================================================
"""

import os
import sys
import subprocess
import re
import time
import firebase_admin
from firebase_admin import credentials, firestore

FIREBASE_KEY_PATH = os.environ.get(
    "FIREBASE_SERVICE_ACCOUNT_PATH",
    "/home/pi/snacker/serviceAccountKey.json"
)

def init_firestore():
    if not firebase_admin._apps:
        if os.path.exists(FIREBASE_KEY_PATH):
            cred = credentials.Certificate(FIREBASE_KEY_PATH)
            firebase_admin.initialize_app(cred)
        else:
            firebase_admin.initialize_app()
    return firestore.client()

def main():
    print("[QUICK-TUNNEL] Connecting to Firebase Firestore...")
    try:
        db = init_firestore()
    except Exception as e:
        print(f"[FATAL] Could not initialize Firebase: {e}")
        sys.exit(1)

    print("[QUICK-TUNNEL] Launching Cloudflare Quick Tunnel forwarding to http://localhost:80...")
    cmd = ["cloudflared", "tunnel", "--url", "http://localhost:80"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
    except FileNotFoundError:
        print("[FATAL] 'cloudflared' binary not found. Please install it with apt or dpkg.")
        sys.exit(1)

    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    url_registered = False

    try:
        # cloudflared prints the tunnel URL to stderr
        for line in proc.stderr:
            sys.stdout.write(line)
            sys.stdout.flush()

            match = url_pattern.search(line)
            if match and not url_registered:
                tunnel_url = match.group(0)
                url_registered = True
                print("\n" + "=" * 65)
                print(f"✨ [DISCOVERED QUICK TUNNEL URL]: {tunnel_url}")
                print("=" * 65)

                # Push to Firestore
                db.collection("config").document("gateway").set({
                    "hostUrl": tunnel_url,
                    "paymentApiUrl": f"{tunnel_url}/api/v1",
                    "checkoutUrl": tunnel_url,
                    "status": "online",
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                })
                print("[FIRESTORE] Live tunnel configuration written to 'config/gateway'!\n")

        proc.wait()
    except KeyboardInterrupt:
        print("\n[STOPPING] Shutting down quick tunnel...")
        proc.terminate()
        try:
            db.collection("config").document("gateway").set({
                "status": "offline",
                "updatedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)
        except Exception:
            pass

if __name__ == "__main__":
    main()
