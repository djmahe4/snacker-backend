# SNACKER: Domain Migration & Production Deployment Guide 🌐🚀

This guide provides end-to-end instructions for transitioning the **SNACKER** system (Frontend PWA, Backend API, Hardware Controller, and YouPI-PG Gateway) from a local development environment (`localhost`) to a **custom production domain** (e.g. `snacker.yourcollege.edu` or `snacker.yourdomain.com`).

---

## 📑 Table of Contents

- [1. Deployment Architecture Options](#1-deployment-architecture-options)
  - [Topology A: Hybrid Production (Vercel Frontend + Pi Gateway & Kiosk)](#topology-a-hybrid-production-recommended)
  - [Topology B: All-in-One Autonomous Kiosk (Everything on Raspberry Pi)](#topology-b-all-in-one-autonomous-kiosk)
- [2. Environment Variable Migration Matrix](#2-environment-variable-migration-matrix)
  - [Frontend PWA (`snacker/.env.local`)](#a-frontend-pwa-envlocal--vercel-env)
  - [Backend & YouPI-PG Gateway (`youpi-pg/.env` & `snacker-backend/.env`)](#b-backend--youpi-pg-gateway-env)
- [3. Third-Party Service Domain Configurations](#3-third-party-service-domain-configurations)
  - [Firebase Console Authorization](#firebase-console-authorized-domains)
  - [Clerk Authentication Redirects](#clerk-authentication-redirects)
- [4. Nginx Production Configurations with SSL](#4-nginx-production-configurations-with-ssl)
  - [Option 1: Unified Single-Domain Multiplexing](#option-1-unified-single-domain-multiplexing)
  - [Option 2: Multi-Subdomain Architecture](#option-2-multi-subdomain-architecture)
  - [Automated SSL with Let's Encrypt Certbot](#automated-ssl-with-lets-encrypt-certbot)
- [5. Cloudflare Tunnel Setup (Zero Port Forwarding)](#5-cloudflare-tunnel-setup-zero-port-forwarding)
- [6. PWA & Service Worker HTTPS Requirements](#6-pwa--service-worker-https-requirements)
- [7. Pre-Flight Verification & Smoke Testing](#7-pre-flight-verification--smoke-testing)

---

## 1. Deployment Architecture Options

Depending on your campus network and hardware setup, choose one of the two standard deployment models:

### Topology A: Hybrid Production (Recommended)
- **Frontend PWA**: Hosted on **Vercel** with worldwide CDN caching (`https://snacker.yourdomain.com`).
- **Physical Kiosk**: Raspberry Pi running on campus Wi-Fi / Ethernet behind a **Cloudflare Tunnel** (`https://pay.yourdomain.com` ➔ Pi `:7000` / `:6100`).
- **Why it's best**: Zero latency for students browsing snacks from anywhere on campus; zero router firewall / port-forwarding configuration required on the college network.

```
[Student Phone] ──► Vercel Edge CDN (snacker.yourdomain.com)
                           │
       ┌───────────────────┴───────────────────┐
       ▼                                       ▼
Firebase Firestore              Cloudflare Tunnel (pay.yourdomain.com)
(Realtime state sync)                          │
                                               ▼
                              Raspberry Pi (Physical Kiosk)
                              ├── YouPI-PG Gateway (:7000 / :6100)
                              ├── Snacker Vending Daemon (USB Scanner)
                              └── Arduino Mega 2560 (Relay & IR Sensor)
```

### Topology B: All-in-One Autonomous Kiosk
- **All components** (Next.js PWA, YouPI-PG, SQLite, Nginx, and Python Vending Daemon) run self-hosted directly on the **Raspberry Pi 4 / 5**.
- Nginx multiplexes all traffic on ports `80` and `443` via a local domain (e.g. `snacker.local` or a static IP/custom domain).

---

## 2. Environment Variable Migration Matrix

When moving from `localhost` to your production domain, update environment variables across both repositories.

### A. Frontend PWA (`snacker/.env.local` / Vercel Env)

| Variable | Local Development Value | Production Domain Value (Hybrid) | Production Domain Value (All-on-Pi) |
| :--- | :--- | :--- | :--- |
| `NEXT_PUBLIC_YOUPI_HOST` | `http://localhost:7000` | `https://pay.yourdomain.com` | `https://snacker.yourdomain.com` |
| `YOUPI_GATEWAY_URL` | `http://localhost:6100` | `https://pay.yourdomain.com` (or Tunnel) | `http://127.0.0.1:6100` |
| `YOUPI_MERCHANT_ID` | `snacker-merchant-01` | `snacker-merchant-01` | `snacker-merchant-01` |
| `YOUPI_WEBHOOK_SECRET` | `snacker_youpi_secret_8899` | *(Strong random 32+ char secret)* | *(Strong random 32+ char secret)* |
| `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | `/sign-up` | `/sign-up` | `/sign-up` |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | `/sign-in` | `/sign-in` | `/sign-in` |
| `NEXT_PUBLIC_CLERK_AFTER_SIGN_UP_URL` | `/api/auth/create-user` | `https://snacker.yourdomain.com/api/auth/create-user` | `/api/auth/create-user` |
| `NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL` | `/` | `https://snacker.yourdomain.com/` | `/` |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | `snacker-app.firebaseapp.com` | `snacker-app.firebaseapp.com` | `snacker-app.firebaseapp.com` |

---

### B. Backend & YouPI-PG Gateway (`youpi-pg/.env`)

Located on the Raspberry Pi at `/home/pi/youpi-pg/.env`:

| Parameter | Local Development | Production Domain (Hybrid) | Production Domain (All-on-Pi) |
| :--- | :--- | :--- | :--- |
| `NODE_ENV` | `development` | `production` | `production` |
| `BASE_PAYMENT_API_URL` | `http://localhost:6100` | `https://pay.yourdomain.com/api/v1` | `https://snacker.yourdomain.com/api/v1` |
| `BASE_CHECKOUT_URL` | `http://localhost:7000` | `https://pay.yourdomain.com` | `https://snacker.yourdomain.com/checkout` |
| `MERCHANT_WEBHOOK_URL` | `http://localhost:3000/api/youpi-webhook` | `https://snacker.yourdomain.com/api/youpi-webhook` | `http://127.0.0.1:3000/api/youpi-webhook` |
| `MERCHANT_WEBHOOK_SECRET`| `snacker_youpi_secret_8899` | *(Must match frontend secret)* | *(Must match frontend secret)* |
| `USE_SQLITE_FALLBACK` | `true` | `true` | `true` |
| `SQLITE_DB_PATH` | `./data/youpi_pg.sqlite` | `./data/youpi_pg.sqlite` | `./data/youpi_pg.sqlite` |
| `PAYMENT_ACTIVE_WINDOW_MS`| `300000` (5 mins) | `300000` (5 mins) | `300000` (5 mins) |

---

## 3. Third-Party Service Domain Configurations

### Firebase Console: Authorized Domains
When deploying on a custom domain, Firebase Authentication and Firestore will block requests unless the new domain is explicitly whitelisted:

1. Open the [Firebase Console](https://console.firebase.google.com/).
2. Select your project ➔ Click **Authentication** ➔ **Settings** tab.
3. Scroll to **Authorized domains**.
4. Click **Add domain** and enter your custom domain:
   - `snacker.yourdomain.com`
   - `pay.yourdomain.com`
5. Click **Save**.

### Clerk Authentication Redirects
If using Clerk for student email login:
1. Open the [Clerk Dashboard](https://dashboard.clerk.com/).
2. Navigate to **Configure** ➔ **Paths**.
3. Under **Production Domain**, configure `https://snacker.yourdomain.com`.
4. Ensure the redirect URLs permit `https://snacker.yourdomain.com/api/auth/create-user`.

---

## 4. Nginx Production Configurations with SSL

### Option 1: Unified Single-Domain Multiplexing
All traffic runs under one single domain (`https://snacker.yourdomain.com`). Nginx splits requests internally based on URL path:

```nginx
# /etc/nginx/sites-available/snacker
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    server_name snacker.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name snacker.yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/snacker.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/snacker.yourdomain.com/privkey.pem;

    client_max_body_size 25M;

    # 1. Main Next.js PWA Web Application
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }

    # 2. YouPI SDK Script
    location = /youpi.js {
        proxy_pass http://127.0.0.1:7000/youpi.js;
        proxy_set_header Host $host;
    }

    # 3. Dynamic UPI Checkout Screen & SSE Stream
    location /checkout/ {
        proxy_pass http://127.0.0.1:7000/checkout/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400s;
    }

    # 4. Payment Core API (REST + SSE Queue status)
    location /api/v1/ {
        proxy_pass http://127.0.0.1:6100/api/v1/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 86400s;
    }

    # 5. Raw Webhook Delivery (Unbuffered for HMAC verification)
    location /api/youpi-webhook {
        proxy_pass http://127.0.0.1:3000/api/youpi-webhook;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_request_buffering off;
    }
}
```

---

### Option 2: Multi-Subdomain Architecture
If preferred, split services cleanly by subdomains:

- `snacker.yourdomain.com` ➔ Next.js PWA (`127.0.0.1:3000`)
- `pay.yourdomain.com` ➔ YouPI Checkout Portal (`127.0.0.1:7000`)
- `api-pay.yourdomain.com` ➔ Payment Core API (`127.0.0.1:6100`)

```nginx
# Checkout Portal
server {
    listen 443 ssl http2;
    server_name pay.yourdomain.com;
    ssl_certificate     /etc/letsencrypt/live/pay.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pay.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:7000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
    }
}

# Central Payment Core API
server {
    listen 443 ssl http2;
    server_name api-pay.yourdomain.com;
    ssl_certificate     /etc/letsencrypt/live/api-pay.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api-pay.yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:6100;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
    }
}
```

---

### Automated SSL with Let's Encrypt Certbot
To generate free, auto-renewing HTTPS certificates on the Pi:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d snacker.yourdomain.com -d pay.yourdomain.com
```

---

## 5. Cloudflare Tunnel Setup (Zero Port Forwarding)

Most campus networks and corporate Wi-Fi connections use **Carrier-Grade NAT (CGNAT)** or block incoming ports `80` and `443`. **Cloudflare Tunnel (`cloudflared`)** exposes the Raspberry Pi's local services securely over HTTPS without requiring open router ports or static public IPs.

### Step 1: Install `cloudflared` on Raspberry Pi (ARM64)
```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared bullseye main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
```

### Step 2: Authenticate and Create Tunnel
```bash
cloudflared tunnel login
cloudflared tunnel create snacker-kiosk
```

### Step 3: Configure Ingress Rules (`/etc/cloudflared/config.yml`)
```yaml
tunnel: <TUNNEL_ID>
credentials-file: /home/pi/.cloudflared/<TUNNEL_ID>.json

ingress:
  # 1. Route pay.yourdomain.com to YouPI Checkout Portal
  - hostname: pay.yourdomain.com
    service: http://localhost:7000

  # 2. Route api-pay.yourdomain.com to Payment Core REST API
  - hostname: api-pay.yourdomain.com
    service: http://localhost:6100

  # 3. Route kiosk-admin.yourdomain.com to Merchant Dashboard
  - hostname: admin-pay.yourdomain.com
    service: http://localhost:6001

  # Catch-all rule
  - service: http_status:404
```

### Step 4: Run as Persistent Background Daemon
```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

Now, `https://pay.yourdomain.com` automatically routes directly to your Raspberry Pi without exposing any ports on your local router!

---

## 6. PWA & Service Worker HTTPS Requirements

> [!IMPORTANT]
> Modern web browsers (Google Chrome, Apple Safari, Mozilla Firefox) **strictly enforce HTTPS** for Progressive Web Apps (PWAs).
> 
> Service Workers (`sw.js`) and Web App Manifests will **fail to install** if served over unencrypted HTTP (e.g. `http://192.168.1.50` or `http://snacker.yourcollege.edu`). The only exception allowed by browsers is `http://localhost`.

### Troubleshooting PWA Domain Migration
If the "Install App" button does not show up after moving to your custom domain:
1. Open Chrome DevTools (`F12`) ➔ **Application** tab ➔ **Manifest**.
2. Verify all icons point to HTTPS URLs.
3. Check the **Service Workers** tab for registration errors.
4. Ensure your domain has a valid, non-expired SSL certificate.

---

## 7. Pre-Flight Verification & Smoke Testing

Before deploying to live students, execute these quick diagnostic commands:

```bash
# 1. Verify YouPI-PG Gateway Health on Pi
curl -I http://localhost:7000/youpi.js
# Expected: HTTP/1.1 200 OK

# 2. Verify Nginx Proxied Endpoint over HTTPS
curl -I https://pay.yourdomain.com/youpi.js
# Expected: HTTP/2 200 OK

# 3. Test Webhook HMAC Signature Verification
cd /home/pi/snacker
npm run test:webhook
# Expected: "Security check passed: Tampered signatures correctly rejected!"

# 4. Verify Live Logs
sudo journalctl -u youpi-pg.service -f
sudo journalctl -u snacker-vending.service -f
```
