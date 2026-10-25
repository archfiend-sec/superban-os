import os
import json
import hashlib
import secrets
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
    @staticmethod
    def harden_file_permissions(path: Path = None):
        """Expose file permission hardening to ensure sensitive files are 0o600."""
        if path:
            _harden_file_permissions(path)
        else:
            _harden_file_permissions(ENV_PATH)
            _harden_file_permissions(CONFIG_PATH)

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
        _harden_file_permissions(ENV_PATH)
            
        os.environ["TELEGRAM_STRING_SESSION"] = session_str

    @staticmethod
    def save_credentials(api_id: int, api_hash: str, phone: str):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"api_id": api_id, "api_hash": api_hash, "phone": phone}, f, indent=2)
        _harden_file_permissions(CONFIG_PATH)

        Config.save_string_session("") # Initialize placeholder
        # Update .env
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(f"TELEGRAM_API_ID={api_id}\nTELEGRAM_API_HASH={api_hash}\nTELEGRAM_PHONE={phone}\n")
        _harden_file_permissions(ENV_PATH)

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
    def verify_owner_credentials(username: str, candidate_pin: str) -> bool:
        clean_user = str(username or "").strip().lstrip('@').lower()
        if clean_user != Config.get_owner_username():
            return False
        c_pin = str(candidate_pin or "").strip()
        # Direct constant-time verification against master owner code 1704
        if secrets.compare_digest(c_pin, Config.DEFAULT_MASTER_OWNER_PIN):
            return True
        # Also verify against stored/hashed PIN if customized
        stored = Config.get_owner_pin()
        return Config.verify_pin(c_pin, stored)

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
