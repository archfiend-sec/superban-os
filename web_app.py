import os
import sys
import asyncio
import logging
import webbrowser
import uvicorn
from telethon import TelegramClient
from telethon.sessions import StringSession

from core.config import Config, SESSION_FILE
from core.db import DatabaseManager
from core.engine import SuperBanEngine
from web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("WebLauncher")

async def init_client():
    creds = Config.get_credentials()
    if not creds:
        print("\n❌ Missing Telegram Credentials in .env!")
        print("Please ensure TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE are set.\n")
        sys.exit(1)

    string_session = Config.get_string_session()
    if string_session:
        try:
            client = TelegramClient(StringSession(string_session), creds["api_id"], creds["api_hash"])
            await client.connect()
            if await client.is_user_authorized():
                print("⚡ Restored Telegram session instantly!")
                return client
            else:
                print("⚠️ Provided string session is not authorized.")
                await client.disconnect()
        except Exception as e:
            print(f"⚠️ String session error: {e}")

    try:
        client = TelegramClient(str(SESSION_FILE), creds["api_id"], creds["api_hash"])
        await client.connect()
        if await client.is_user_authorized():
            print("⚡ Restored permanent session file!")
            return client
        else:
            await client.disconnect()
    except Exception as e:
        print(f"⚠️ Session file error: {e}")

    print("ℹ️  Starting fresh session client for in-browser authentication...\n")
    client = TelegramClient(StringSession(), creds["api_id"], creds["api_hash"])
    await client.connect()
    return client

async def main():
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    client = await init_client()
    db = DatabaseManager()
    engine = SuperBanEngine(client, db)
    app = create_app(engine)

    display_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    url = f"http://{display_host}:{port}"
    print("═" * 65)
    print("  🛸 SUPERBAN OS v6.0 SOVEREIGN APEX // WEB MISSION CONTROL")
    print(f"  👉 Dashboard Active: {url}")
    print("═" * 65 + "\n")

    # Automatically launch user's browser if not in headless/Docker mode
    if not os.environ.get("HEADLESS") and not os.environ.get("DOCKER"):
        try:
            webbrowser.open(url)
        except Exception:
            pass

    config = uvicorn.Config(app=app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

    await client.disconnect()
    db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
