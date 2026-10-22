# ☁️ SuperBan OS Cloud Deployment Guide

This guide covers deploying SuperBan OS to production environments for 24/7 autonomous monitoring.

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Method 1: Docker on Linux VPS (Recommended)](#method-1-docker-on-linux-vps-recommended)
3. [Method 2: Systemd Service on Ubuntu / Debian](#method-2-systemd-service-on-ubuntu--debian)
4. [Method 3: Cloudflare Tunnel (Expose Local Mac / Server via Public HTTPS)](#method-3-cloudflare-tunnel-expose-local-mac--server-via-public-https)
5. [Method 4: Cloud Containers (Railway / Render)](#method-4-cloud-containers-railway--render)
6. [Nginx Reverse Proxy with SSL (Certbot)](#nginx-reverse-proxy-with-ssl-certbot)

---

## Prerequisites

Before deploying to any remote server, ensure you have:
1. Your **Telegram API ID** & **API Hash** from [my.telegram.org](https://my.telegram.org).
2. A **Telethon String Session**: Log in locally once to obtain your `TELEGRAM_STRING_SESSION` from `.env`. This allows headless cloud servers to boot immediately without requiring SMS 2FA codes.

---

## Method 1: Docker on Linux VPS (Recommended)

Works on any Ubuntu 22.04 / 24.04 or Debian server (DigitalOcean Droplet, Hetzner, AWS EC2, Linode).

### 1. Install Docker & Docker Compose
```bash
sudo apt update && sudo apt install -y curl git
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
```

### 2. Clone the Repository
```bash
git clone https://github.com/yourusername/superban-os.git /opt/superban-os
cd /opt/superban-os
```

### 3. Configure `.env`
```bash
cp .env.example .env
nano .env
```
Fill in:
```env
TELEGRAM_API_ID=your_api_id
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_PHONE=+your_phone_number
TELEGRAM_STRING_SESSION=your_saved_string_session
PORT=8000
HOST=0.0.0.0
HEADLESS=1
```

### 4. Build & Launch in Background
```bash
docker compose up -d --build
```

### 5. Verify Running Container
```bash
docker compose logs -f
```
Your dashboard will be accessible at `http://your-server-ip:8000`.

---

## Method 2: Systemd Service on Ubuntu / Debian

For lightweight native execution without Docker.

### 1. Install System Dependencies
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv ffmpeg libopus-dev git
```

### 2. Clone and Setup Environment
```bash
git clone https://github.com/yourusername/superban-os.git /opt/superban-os
cd /opt/superban-os
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env # Paste your credentials and StringSession
```

### 3. Create Systemd Service File
```bash
sudo nano /etc/systemd/system/superban.service
```

Paste the following configuration:
```ini
[Unit]
Description=SuperBan OS Sovereign Web Mission Control
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/superban-os
ExecStart=/opt/superban-os/venv/bin/python3 web_app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=HEADLESS=1

[Install]
WantedBy=multi-user.target
```

### 4. Enable and Start Service
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now superban
sudo systemctl status superban
```

---

## Method 3: Cloudflare Tunnel (Expose Local Mac / Server via Public HTTPS)

If you are running SuperBan OS on your Mac or local home server and want to access it securely from your phone anywhere in the world without opening router ports or static IPs:

1. Install `cloudflared`:
   * **macOS**: `brew install cloudflared`
   * **Linux**: `sudo apt install -y cloudflared`

2. Run a Quick Tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
3. Cloudflare will output a public HTTPS URL (e.g. `https://random-subdomain.trycloudflare.com`).
4. Open that URL on your phone to access your full Web Mission Control dashboard securely over encrypted HTTPS.

---

## Method 4: Cloud Containers (Railway / Render)

### Railway
1. Fork or push your SuperBan OS repository to GitHub.
2. Log in to [Railway.app](https://railway.app) and select **New Project** -> **Deploy from GitHub repo**.
3. In **Variables**, add:
   * `TELEGRAM_API_ID`
   * `TELEGRAM_API_HASH`
   * `TELEGRAM_PHONE`
   * `TELEGRAM_STRING_SESSION`
   * `PORT=8000`
   * `HEADLESS=1`
4. In **Settings**, generate a public domain (e.g. `superban-production.up.railway.app`).
5. Railway will automatically detect the `Dockerfile` and deploy the service.

---

## Nginx Reverse Proxy with SSL (Certbot)

To bind your VPS deployment to a custom domain with free HTTPS:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
```

Configure `/etc/nginx/sites-available/superban`:
```nginx
server {
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and obtain SSL certificate:
```bash
sudo ln -s /etc/nginx/sites-available/superban /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com
```
