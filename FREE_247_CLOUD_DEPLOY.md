# 🌐 100% FREE 24/7 Cloud Deployment Guide (SuperBan OS)

This guide shows you how to host SuperBan OS completely **100% FREE** so that it stays online **24 hours a day, 7 days a week, 365 days a year**, even when your Mac is turned off, sleeping, or disconnected from the internet.

---

## 🏆 The Best 100% Free Cloud Platform: Render.com

[Render](https://render.com) offers a free cloud tier that builds directly from your GitHub repository using Docker. It provides:
- **$0 / Month (100% Free Forever)**
- **Permanent HTTPS Domain**: e.g. `https://superban-apex.onrender.com`
- **Zero Server Maintenance**: Render handles the Linux environment, security, and networking.

---

## 📋 3-Step Walkthrough (Takes ~3 Minutes)

### Step 1: Push Your Code to a Free GitHub Repository

If you haven't pushed yet:
1. Go to [github.com/new](https://github.com/new) and log in (or create a free account).
2. Name your repository (e.g. `superban-os`), set it to **Private** (recommended) or Public.
3. **Do NOT** check "Add a README file" or ".gitignore".
4. Click **Create repository**.
5. In your Mac Terminal, run:
   ```bash
   cd /Users/archfiend/.gemini/antigravity/scratch/telegram_group_manager
   git remote add origin https://github.com/YOUR_USERNAME/superban-os.git
   git push -u origin main
   ```

---

### Step 2: Deploy on Render (100% Free)

1. Go to **[dashboard.render.com](https://dashboard.render.com/)** and sign in with your GitHub account.
2. Click **New +** in the top right and select **Web Service**.
3. Choose **Build and deploy from a Git repository**.
4. Select your `superban-os` repository.
5. Configure the service:
   - **Name**: `superban-os` (or whatever you prefer)
   - **Region**: Choose the closest to you (e.g., Oregon, Frankfurt, Singapore)
   - **Runtime**: **Docker** (it will automatically detect the `Dockerfile` in your repo)
   - **Instance Type**: **Free** ($0/month)
6. Scroll down to **Environment Variables** and add the following 4 keys (copied from your local `.env`):
   - `TELEGRAM_API_ID`
   - `TELEGRAM_API_HASH`
   - `TELEGRAM_PHONE`
   - `TELEGRAM_STRING_SESSION` *(This is your pre-authorized token so no SMS code is needed!)*
   - `HEADLESS`: `1`
7. Click **Deploy Web Service**!

Render will build the container and within 2–3 minutes give you a permanent live HTTPS URL:
👉 `https://superban-os-xxxx.onrender.com`

---

### Step 3: Keep It Awake 24/7 (Prevent Free Sleep)

Render's free tier spins down if no web requests are received for 15 minutes. To keep it active 24/7/365 without it ever sleeping:
1. Go to **[uptimerobot.com](https://uptimerobot.com/)** (100% Free).
2. Click **Add New Monitor**:
   - **Monitor Type**: `HTTP(s)`
   - **Friendly Name**: `SuperBan OS 24/7`
   - **URL**: `https://your-app-name.onrender.com/api/status`
   - **Monitoring Interval**: `Every 10 minutes`
3. Click **Create Monitor**.

🎉 **Done!** UptimeRobot will ping your dashboard every 10 minutes, keeping your cloud container permanently awake and monitoring Telegram 24/7, even when your Mac is turned off!
