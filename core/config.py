import os
import json
import hashlib
import secrets
from typing import Optional, Dict, Any, Union
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "ultimate_config.json"
SESSION_DIR = BASE_DIR / "sessions"
SESSION_DIR.mkdir(exist_ok=True)
SESSION_FILE = SESSION_DIR / "superban"

load_dotenv(ENV_PATH)

def _harden_file_permissions(path: Path):
    """Enforce strict read/write owner-only permissions (0600) on sensitive credential files."""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except Exception:
        pass

# Ensure existing secret files on disk are immediately locked down to owner-only access
_harden_file_permissions(ENV_PATH)
_harden_file_permissions(CONFIG_PATH)

class Config:
    DEFAULT_API_ID = 2040
    DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"

    @staticmethod
    def harden_file_permissions(path: Path = None):
        """Expose file permission hardening to ensure sensitive files are 0o600."""
        if path:
            _harden_file_permissions(path)
        else:
            _harden_file_permissions(ENV_PATH)
            _harden_file_permissions(CONFIG_PATH)

    @staticmethod
    def get_api_credentials():
        """Retrieve API ID and API Hash from env, config file, or official Telegram defaults."""
        load_dotenv(ENV_PATH, override=True)
        raw_id = os.environ.get("TELEGRAM_API_ID", "").strip()
        api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
        api_id = None
        if raw_id:
            try:
                api_id = int(raw_id)
            except ValueError:
                pass

        if not api_id or not api_hash:
            if CONFIG_PATH.exists():
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("api_id") and data.get("api_hash"):
                            api_id = int(data["api_id"])
                            api_hash = str(data["api_hash"]).strip()
                except Exception:
                    pass

        if not api_id or not api_hash:
            api_id = Config.DEFAULT_API_ID
            api_hash = Config.DEFAULT_API_HASH

        return {"api_id": api_id, "api_hash": api_hash}

    @staticmethod
    def get_credentials(allow_empty_phone: bool = True):
        """Retrieve credentials dict with api_id, api_hash, and phone."""
        # Reload env in case it was updated
        load_dotenv(ENV_PATH, override=True)
        api_creds = Config.get_api_credentials()
        phone = os.environ.get("TELEGRAM_PHONE", "").strip()

        if not phone and CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    phone = str(data.get("phone", "")).strip()
            except Exception:
                pass

        if phone or allow_empty_phone:
            return {
                "api_id": api_creds["api_id"],
                "api_hash": api_creds["api_hash"],
                "phone": phone
            }
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
        _harden_file_permissions(ENV_PATH)
            
        os.environ["TELEGRAM_STRING_SESSION"] = session_str

    @staticmethod
    def save_phone(phone: str):
        """Persist phone number into CONFIG_PATH and .env without erasing other keys."""
        phone = phone.strip()
        if not phone:
            return
        data = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["phone"] = phone
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _harden_file_permissions(CONFIG_PATH)

        env_lines = []
        found = False
        if ENV_PATH.exists():
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TELEGRAM_PHONE="):
                        env_lines.append(f"TELEGRAM_PHONE={phone}\n")
                        found = True
                    else:
                        env_lines.append(line)
        if not found:
            env_lines.append(f"TELEGRAM_PHONE={phone}\n")

        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(env_lines)
        _harden_file_permissions(ENV_PATH)
        os.environ["TELEGRAM_PHONE"] = phone

    @staticmethod
    def save_credentials(api_id: int, api_hash: str, phone: str = ""):
        """Persist api_id, api_hash, and optional phone to CONFIG_PATH and .env without erasing OWNER_PIN."""
        data = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["api_id"] = api_id
        data["api_hash"] = api_hash
        if phone:
            data["phone"] = phone
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _harden_file_permissions(CONFIG_PATH)

        env_lines = []
        seen_keys = set()
        if ENV_PATH.exists():
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TELEGRAM_API_ID="):
                        env_lines.append(f"TELEGRAM_API_ID={api_id}\n")
                        seen_keys.add("TELEGRAM_API_ID")
                    elif line.startswith("TELEGRAM_API_HASH="):
                        env_lines.append(f"TELEGRAM_API_HASH={api_hash}\n")
                        seen_keys.add("TELEGRAM_API_HASH")
                    elif phone and line.startswith("TELEGRAM_PHONE="):
                        env_lines.append(f"TELEGRAM_PHONE={phone}\n")
                        seen_keys.add("TELEGRAM_PHONE")
                    else:
                        env_lines.append(line)
        if "TELEGRAM_API_ID" not in seen_keys:
            env_lines.append(f"TELEGRAM_API_ID={api_id}\n")
        if "TELEGRAM_API_HASH" not in seen_keys:
            env_lines.append(f"TELEGRAM_API_HASH={api_hash}\n")
        if phone and "TELEGRAM_PHONE" not in seen_keys:
            env_lines.append(f"TELEGRAM_PHONE={phone}\n")

        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(env_lines)
        _harden_file_permissions(ENV_PATH)

        os.environ["TELEGRAM_API_ID"] = str(api_id)
        os.environ["TELEGRAM_API_HASH"] = str(api_hash)
        if phone:
            os.environ["TELEGRAM_PHONE"] = str(phone)

    @staticmethod
    def hash_pin(pin: str, salt: bytes = None) -> str:
        """Hash a PIN using PBKDF2-HMAC-SHA256 with 600,000 iterations and a cryptographically secure salt."""
        pin_str = str(pin).strip()
        if not pin_str:
            return ""
        if salt is None:
            salt = secrets.token_bytes(16)
        iterations = 600000
        key = hashlib.pbkdf2_hmac("sha256", pin_str.encode("utf-8"), salt, iterations)
        return f"pbkdf2_sha256${iterations}${salt.hex()}${key.hex()}"

    @staticmethod
    def verify_pin(candidate_pin: str, stored_hash_or_plain: str) -> bool:
        """Constant-time verification of a candidate PIN against a PBKDF2 hash or legacy plaintext."""
        candidate = str(candidate_pin).strip()
        stored = str(stored_hash_or_plain).strip()
        if not candidate or not stored:
            return False

        if stored.startswith("pbkdf2_sha256$"):
            try:
                parts = stored.split("$")
                if len(parts) == 4:
                    iterations = int(parts[1])
                    salt = bytes.fromhex(parts[2])
                    expected_key = parts[3]
                    computed = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), salt, iterations).hex()
                    return secrets.compare_digest(computed, expected_key)
            except Exception:
                return False

        # Legacy fallback: constant-time comparison for plaintext PINs
        return secrets.compare_digest(candidate, stored)

    MASTER_OWNER_ID: int = 8312795263
    MASTER_OWNER_USERNAME = "archfiend000"
    DEFAULT_MASTER_OWNER_PIN = "1704"

    @staticmethod
    def get_owner_telegram_id() -> int:
        try:
            return int(os.environ.get("MASTER_OWNER_TELEGRAM_ID", 8312795263))
        except (ValueError, TypeError):
            return 8312795263

    @staticmethod
    def get_owner_username() -> str:
        return os.environ.get("MASTER_OWNER_USERNAME", "archfiend000").strip().lstrip('@').lower()

    @staticmethod
    def verify_owner_credentials(username: Optional[str], candidate_pin: str) -> bool:
        c_pin = str(candidate_pin or "").strip()
        if not c_pin:
            return False
        # Direct constant-time verification against master owner code 1704 or stored PBKDF2 hash
        if secrets.compare_digest(c_pin, Config.DEFAULT_MASTER_OWNER_PIN) or Config.verify_pin(c_pin, Config.get_owner_pin()):
            return True
        return False

    @staticmethod
    def get_owner_pin() -> str:
        load_dotenv(ENV_PATH, override=True)
        pin = os.environ.get("OWNER_PIN", "").strip() or os.environ.get("OWNER_PASSWORD", "").strip()
        if pin:
            return pin
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    val = str(data.get("owner_pin", "")).strip()
                    if val:
                        return val
            except Exception:
                pass
        return Config.DEFAULT_MASTER_OWNER_PIN

    @staticmethod
    def save_owner_pin(pin: str):
        raw = str(pin).strip()
        if not raw:
            return
        # If already hashed with PBKDF2, keep as-is; otherwise hash before saving
        if raw.startswith("pbkdf2_sha256$"):
            stored_val = raw
        else:
            stored_val = Config.hash_pin(raw)

        data = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["owner_pin"] = stored_val
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _harden_file_permissions(CONFIG_PATH)

        env_lines = []
        found = False
        if ENV_PATH.exists():
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("OWNER_PIN="):
                        env_lines.append(f"OWNER_PIN={stored_val}\n")
                        found = True
                    else:
                        env_lines.append(line)
        if not found:
            env_lines.append(f"OWNER_PIN={stored_val}\n")
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(env_lines)
        _harden_file_permissions(ENV_PATH)
        os.environ["OWNER_PIN"] = stored_val
