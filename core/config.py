import os
import json
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "ultimate_config.json"
SESSION_DIR = BASE_DIR / "sessions"
SESSION_DIR.mkdir(exist_ok=True)
SESSION_FILE = SESSION_DIR / "superban"

load_dotenv(ENV_PATH)

class Config:
    @staticmethod
    def get_credentials():
        # Reload env in case it was updated
        load_dotenv(ENV_PATH, override=True)
        api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
        api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
        phone = os.environ.get("TELEGRAM_PHONE", "").strip()

        if api_id and api_hash and phone:
            return {
                "api_id": int(api_id),
                "api_hash": api_hash,
                "phone": phone
            }

        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("api_id") and data.get("api_hash") and data.get("phone"):
                        return data
            except Exception:
                pass
        return None

    @staticmethod
    def get_string_session() -> str:
        load_dotenv(ENV_PATH, override=True)
        return os.environ.get("TELEGRAM_STRING_SESSION", "").strip()

    @staticmethod
    def save_string_session(session_str: str):
        """Persist session string directly into .env so logins are never needed again."""
        env_lines = []
        found = False
        if ENV_PATH.exists():
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TELEGRAM_STRING_SESSION="):
                        env_lines.append(f"TELEGRAM_STRING_SESSION={session_str}\n")
                        found = True
                    else:
                        env_lines.append(line)
        if not found:
            env_lines.append(f"TELEGRAM_STRING_SESSION={session_str}\n")

        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(env_lines)
            
        os.environ["TELEGRAM_STRING_SESSION"] = session_str

    @staticmethod
    def save_credentials(api_id: int, api_hash: str, phone: str):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"api_id": api_id, "api_hash": api_hash, "phone": phone}, f, indent=2)

        Config.save_string_session("") # Initialize placeholder
        # Update .env
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(f"TELEGRAM_API_ID={api_id}\nTELEGRAM_API_HASH={api_hash}\nTELEGRAM_PHONE={phone}\n")
