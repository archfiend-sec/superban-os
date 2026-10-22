<div align="center">

# 🛸 SUPERBAN OS v6.0 // SOVEREIGN APEX
### Autonomous Non-Admin Cyber War Room & Telecommunications Intelligence Workstation

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Telethon](https://img.shields.io/badge/Telethon-1.36+-2CA5E0.svg?style=for-the-badge&logo=telegram&logoColor=white)](https://github.com/LonamiWebs/Telethon)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

*Operates at maximum tactical power as a standard group participant — **zero Telegram group administrator rights required**.*

</div>

---

## 🌟 Sovereign Non-Admin Superpowers

SuperBan OS leverages native **MTProto** client protocols to process, index, and intercept the real-time event stream of every Telegram group your account participates in.

### 1. 👻 Ghost Vault (Anti-Delete & Anti-Edit Wiretap)
* **Permanent Local Snapshot**: Intercepts and snapshots every message the millisecond it arrives.
* **Deleted Message Recovery**: When a sender or administrator deletes a message, the `MessageDeleted` event triggers. SuperBan OS preserves the unedited original text and exact deletion timestamp locally.
* **Covert Edit Revision Tracking**: Detects alterations and logs the original text alongside the new revision side-by-side.

### 2. 🌐 Social Influence & Relationship Topology Graph
* **Bidirectional Interaction Mapping**: Tracks directional replies (`reply_to_msg_id`) and `@mentions` across the group.
* **Interactive Force-Directed Physics Canvas**:
  * Smooth mouse-wheel zoom and click-and-drag panning.
  * Node sizes and neon glow indicate community authority and degree centrality.
  * Hovering any node highlights its conversation links and dims unrelated users to trace communication flows without clutter.
  * Click any node to open their **Cross-Group OSINT Dossier**.

### 3. ⚡ Signal & IOC Entity Extractor
* **Autonomous Ingestion**: Real-time regex extraction from every message:
  * **Solana Wallets** (`[1-9A-HJ-NP-Za-km-z]{32,44}`)
  * **Ethereum / EVM Wallets** (`0x[a-fA-F0-9]{40}`)
  * **Bitcoin Addresses** (`bc1...`, `1...`, `3...`)
  * **TON Addresses** (`EQ...`, `UQ...`)
  * **Telegram Invites** (`t.me/+...`, `t.me/joinchat/...`)
  * **Cashtags & Tickers** (`$SOL`, `$BTC`, `$PEPE`)
  * **Phishing URLs & Shortlinks**
* **Instant Forensics**: One-click clipboard copy and deep links to Solscan, Etherscan, Tonscan, and Blockchain.com.

### 4. 🛸 Autonomous Siphon Matrix (Phone Push via Saved Messages)
* **Zero-Permission Phone Push**: Uses your own Telegram **Saved Messages** (`me`) as a push alert channel.
* **Autonomous Push Rules**:
  * **All Deleted Messages**: Instantly forwards deleted messages to your phone.
  * **Crypto Wallets (Any)**: Alerts you the second any crypto address is dropped in monitored chats.
  * **Keyword / Target User**: Alerts you when target words or handles post.

### 5. ⏪ Tactical Voice Chat DVR & Instant Replay (30s)
* **Instant Replay (`Replay 30s`)**: Slices and plays back the last 30 seconds of live conversation via FFmpeg stream extraction.
* **Full Session MP3 Archive**: Download complete recorded voice chat streams.
* **Speaker Talk-Time Leaderboard**: Tracks cumulative speech duration per participant and ranks them by total seconds spoken and share of voice percentage (`%`).

### 6. 🔎 Global Forensic Full-Text Search Engine
* Sub-millisecond queries across your local archive of intercepted messages, deleted originals, edit revisions, and sender handles.
* Search across all monitored supergroups simultaneously.

### 7. 🔮 Group Vibe & NLP Sentiment Radar
* Real-time crowd atmosphere analysis: **HYPE / BULLISH**, **HEATED DEBATE**, **HOSTILE / TOXIC**, or **CHILL / CASUAL**.
* Hostility Index and Hype Percentage metrics.
* Surfaces the top 8 repeating keywords and dominant conversation drivers.

### 8. 🛡️ DEFCON Defense Matrix & Sentinel Shield
* 5-tier DEFCON posture controller from Peace (5) to Nuclear Lockdown (1).
* High-velocity heuristic threat neutralization for drainers, spam, and homoglyphs.

---

## 🚀 Quickstart

### Method 1: Docker Compose (Recommended)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/superban-os.git
   cd superban-os
   ```

2. **Configure your environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE
   ```

3. **Launch with Docker Compose**:
   ```bash
   docker compose up -d --build
   ```

4. **Access the Mission Control Dashboard**:
   Open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

### Method 2: Manual Local Setup (Python 3.10+)

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/superban-os.git
   cd superban-os
   ```

2. **Set up a Python Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Install FFmpeg** (Required for Voice Chat DVR & Instant Replay):
   * **macOS**: `brew install ffmpeg`
   * **Ubuntu/Debian**: `sudo apt update && sudo apt install -y ffmpeg libopus-dev`
   * **Windows**: `winget install Gyan.FFmpeg`

5. **Configure `.env`**:
   ```bash
   cp .env.example .env
   # Add your Telegram API credentials
   ```

6. **Launch SuperBan OS**:
   ```bash
   python3 web_app.py
   ```

---

## ⚙️ Configuration (.env)

| Variable | Description | Default |
| :--- | :--- | :--- |
| `TELEGRAM_API_ID` | Telegram API ID from [my.telegram.org](https://my.telegram.org) | *Required* |
| `TELEGRAM_API_HASH` | Telegram API Hash from [my.telegram.org](https://my.telegram.org) | *Required* |
| `TELEGRAM_PHONE` | International phone number (e.g. `+1234567890`) | *Required* |
| `TELEGRAM_STRING_SESSION` | Telethon StringSession (auto-persisted after first login) | *Optional* |
| `HOST` | Binding IP address for the web server | `0.0.0.0` |
| `PORT` | Web Mission Control port | `8000` |
| `HEADLESS` | Set to `1` when running on headless cloud servers | `0` |

---

## ☁️ Cloud Deployment

For detailed production instructions on deploying to **Ubuntu/Debian VPS**, **Railway**, **Render**, **DigitalOcean**, or setting up **Nginx + SSL / Cloudflare Tunnels**, see:
👉 [**DEPLOY.md**](DEPLOY.md)

---

## 🔒 Security & Privacy Notice

* **Zero Cloud Data Leakage**: All intercepted messages, deleted originals, and OSINT profiles are stored strictly in your local SQLite database (`ultimate_manager.db`). No telemetry or data is sent to external servers.
* **Strict Credential Protection**: The `.gitignore` and `.dockerignore` files are pre-configured to prevent `.env`, session files, and databases from ever being committed to version control.
* **Session Persistence**: Once authenticated, the Telethon `StringSession` is stored in `.env`, allowing the application to reboot seamlessly without SMS codes.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
