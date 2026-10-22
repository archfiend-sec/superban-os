import sys
import argparse
import asyncio
import logging
import uvicorn
from telethon import TelegramClient
from telethon.sessions import StringSession

from core.config import Config, SESSION_FILE
from core.db import DatabaseManager
from core.engine import SuperBanEngine
from cli.tui import RichTUI
from web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("SuperBanLauncher")

async def init_client(interactive_cli: bool = False):
    creds = Config.get_credentials()
    if not creds:
        print("\n❌ Missing Telegram Credentials in .env!")
        print("Please ensure TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE are set.\n")
        sys.exit(1)

    string_session = Config.get_string_session()
    
    # 1. Try StringSession first (in-memory zero-prompt restore)
    if string_session:
        print("⚡ Restoring session from TELEGRAM_STRING_SESSION in .env...")
        client = TelegramClient(StringSession(string_session), creds["api_id"], creds["api_hash"])
        await client.connect()
        if await client.is_user_authorized():
            print("🎉 Authentication verified instantly! (Zero prompts required)\n")
            return client
        else:
            print("⚠️ Saved string session expired or revoked.")

    # 2. Try absolute-path file session fallback
    client = TelegramClient(str(SESSION_FILE), creds["api_id"], creds["api_hash"])
    await client.connect()

    if await client.is_user_authorized():
        print("🎉 Authentication verified from permanent session file! (Zero prompts required)\n")
        return client

    # If CLI mode, prompt in terminal. If Web mode, let the browser handle it!
    if interactive_cli:
        print(f"\n🔐 One-time authentication needed for {creds['phone']}.")
        print("ℹ️  Telegram will send a code to your Telegram app / SMS.")
        await client.start(phone=creds["phone"])
        print("\n✅ Verification successful!")

        # Export to StringSession
        try:
            temp_session = StringSession()
            temp_session.set_dc(client.session.dc_id, client.session.server_address, client.session.port)
            temp_session.auth_key = client.session.auth_key
            saved_str = temp_session.save()
            if saved_str:
                Config.save_string_session(saved_str)
                print("💾 Session permanently saved to .env!\n")
        except Exception as e:
            logger.debug(f"String session export notice: {e}")
    else:
        print(f"ℹ️  Telegram authorization will be handled directly in the Web UI.\n")

    return client

async def main():
    parser = argparse.ArgumentParser(description="SuperBan OS v3.2 - Mission Control")
    parser.add_argument("--mode", choices=["web", "cli"], default="web", help="Interface mode (default: web)")
    parser.add_argument("--port", type=int, default=8000, help="Web server port (default 8000)")
    args = parser.parse_args()

    mode = args.mode or "web"
    is_cli = (mode == "cli")

    client = await init_client(interactive_cli=is_cli)
    db = DatabaseManager()
    engine = SuperBanEngine(client, db)

    if mode == "web":
        app = create_app(engine)
        print(f"═" * 65)
        print(f"  🛸 SUPERBAN OS // WEB MISSION CONTROL ACTIVE")
        print(f"  👉 Open in your browser: http://127.0.0.1:{args.port}")
        print(f"═" * 65 + "\n")
        config = uvicorn.Config(app=app, host="127.0.0.1", port=args.port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    else:
        tui = RichTUI(engine)
        await tui.run()

    await client.disconnect()
    db.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
