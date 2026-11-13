import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import json
import sys
from pathlib import Path
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config, ENV_PATH, CONFIG_PATH
from core.engine import SuperBanEngine
from web.app import create_app, OPERATOR_SESSIONS

class AsgiResponse:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.status_code = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8")

class InProcessAsgiClient:
    def __init__(self, app):
        self.app = app

    async def request(
        self,
        method: str,
        path: str,
        headers: dict = None,
        json_data: dict = None,
        body: bytes = b"",
        query_string: str = "",
        client_ip: str = "127.0.0.1"
    ) -> AsgiResponse:
        headers = headers or {}
        req_headers = {}
        for k, v in headers.items():
            req_headers[k.lower()] = v

        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
            req_headers["content-type"] = "application/json"
            req_headers["content-length"] = str(len(body))
        elif body and "content-length" not in req_headers:
            req_headers["content-length"] = str(len(body))

        if "host" not in req_headers:
            req_headers["host"] = "testserver"

        raw_headers = [
            (k.encode("latin1"), v.encode("latin1"))
            for k, v in req_headers.items()
        ]

        if "?" in path and not query_string:
            path, query_string = path.split("?", 1)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": raw_headers,
            "client": (client_ip, 50000),
            "server": ("127.0.0.1", 80),
            "scheme": "http",
        }

        response_status = None
        response_headers = {}
        response_body = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            nonlocal response_status, response_headers, response_body
            if message["type"] == "http.response.start":
                response_status = message["status"]
                for k, v in message.get("headers", []):
                    response_headers[k.decode("latin1").lower()] = v.decode("latin1")
            elif message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(response_status, response_headers, b"".join(response_body))


class TestAuthFlow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create a mock engine and client
        self.mock_client = MagicMock()
        self.mock_client.is_connected.return_value = True
        self.mock_client.is_user_authorized = AsyncMock(return_value=False)
        self.mock_client.connect = AsyncMock()
        self.mock_client.disconnect = AsyncMock()
        self.mock_client.get_me = AsyncMock(return_value=None)
        
        mock_send_res = MagicMock()
        mock_send_res.phone_code_hash = "mock_hash_12345"
        self.mock_client.send_code_request = AsyncMock(return_value=mock_send_res)
        self.mock_client.sign_in = AsyncMock()
        self.mock_client.session = MagicMock()
        self.mock_client.session.dc_id = 4
        self.mock_client.session.server_address = "149.154.167.91"
        self.mock_client.session.port = 443
        self.mock_client.session.auth_key = b"0" * 256

        self.mock_engine = MagicMock(spec=SuperBanEngine)
        self.mock_engine.client = self.mock_client
        self.mock_engine.db = None
        self.mock_engine.register_listener = MagicMock()
        self.mock_engine.sentinel = MagicMock()
        self.mock_engine.sentinel.register_callback = MagicMock()
        self.mock_engine.get_dialogs = AsyncMock(return_value=[])

        self.app = create_app(self.mock_engine)
        self.client = InProcessAsgiClient(self.app)

        self.owner_token = "test_auth_owner_token_999"
        OPERATOR_SESSIONS[self.owner_token] = {
            "username": "archfiend000",
            "role": "owner",
            "expires": 9999999999.0
        }
        self.headers = {"X-Owner-Token": self.owner_token}

    def test_config_api_credentials_fallback(self):
        """Verify Config.get_api_credentials falls back to official keys when env is empty."""
        api_creds = Config.get_api_credentials()
        self.assertIn("api_id", api_creds)
        self.assertIn("api_hash", api_creds)
        self.assertEqual(api_creds["api_id"], Config.DEFAULT_API_ID)
        self.assertEqual(api_creds["api_hash"], Config.DEFAULT_API_HASH)

    def test_config_save_phone_preserves_env(self):
        """Verify saving phone updates TELEGRAM_PHONE without erasing existing keys."""
        initial_pin = Config.get_owner_pin()
        test_phone = "+19998887777"
        Config.save_phone(test_phone)
        
        # Verify phone is saved
        creds = Config.get_credentials()
        self.assertEqual(creds["phone"], test_phone)
        
        # Verify owner pin was not wiped
        self.assertEqual(Config.get_owner_pin(), initial_pin)

    async def test_auth_status_unauthorized(self):
        """Test GET /api/auth/status returns unauthorized state with phone."""
        res = await self.client.request("GET", "/api/auth/status", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["authorized"])
        self.assertIn("phone", data)

    async def test_send_code_missing_phone(self):
        """Test POST /api/auth/send_code fails cleanly if no phone is supplied or stored."""
        with patch.dict(os.environ, {"TELEGRAM_PHONE": ""}):
            with patch.object(Config, "get_credentials", return_value={"api_id": 2040, "api_hash": "abc", "phone": ""}):
                res = await self.client.request("POST", "/api/auth/send_code", headers=self.headers, json_data={})
                self.assertEqual(res.status_code, 400)
                self.assertIn("phone number", res.json()["detail"].lower())

    async def test_send_code_success(self):
        """Test POST /api/auth/send_code succeeds when phone is supplied in request."""
        res = await self.client.request("POST", "/api/auth/send_code", headers=self.headers, json_data={"phone": "+1234567890"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "code_sent")
        self.assertEqual(data["phone"], "+1234567890")
        self.mock_client.send_code_request.assert_called_with("+1234567890")

    async def test_verify_code_2fa_needed(self):
        """Test POST /api/auth/verify_code handles 2FA challenge cleanly."""
        self.mock_client.sign_in.side_effect = SessionPasswordNeededError(request=None)
        res = await self.client.request("POST", "/api/auth/verify_code", headers=self.headers, json_data={"code": "12345", "phone": "+1234567890"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "password_needed")

    async def test_verify_code_success(self):
        """Test POST /api/auth/verify_code completes authorization successfully."""
        self.mock_client.sign_in.side_effect = None
        mock_user = MagicMock()
        mock_user.id = 987654321
        mock_user.first_name = "SuperBan"
        mock_user.username = "superban_bot"
        self.mock_client.get_me = AsyncMock(return_value=mock_user)

        res = await self.client.request("POST", "/api/auth/verify_code", headers=self.headers, json_data={"code": "12345", "phone": "+1234567890"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "authorized")
        self.assertEqual(data["user"]["id"], 987654321)

    async def test_import_session_invalid(self):
        """Test POST /api/auth/import_session handles invalid string sessions cleanly."""
        res = await self.client.request("POST", "/api/auth/import_session", headers=self.headers, json_data={"session_string": "invalid_session_string"})
        self.assertEqual(res.status_code, 400)

    @patch("web.app.TelegramClient")
    async def test_import_session_success(self, mock_tg_client_cls):
        """Test POST /api/auth/import_session successfully imports valid session and updates engine."""
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock()
        mock_instance.disconnect = AsyncMock()
        mock_instance.is_user_authorized = AsyncMock(return_value=True)
        mock_user = MagicMock()
        mock_user.id = 11223344
        mock_user.first_name = "Imported"
        mock_user.username = "imported_bot"
        mock_user.phone = "+19997776666"
        mock_instance.get_me = AsyncMock(return_value=mock_user)
        mock_tg_client_cls.return_value = mock_instance

        valid_dummy_str = "1BJWap1sBuzAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
        res = await self.client.request("POST", "/api/auth/import_session", headers=self.headers, json_data={"session_string": valid_dummy_str})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "authorized")
        self.assertEqual(data["user"]["id"], 11223344)
        self.assertEqual(Config.get_string_session(), valid_dummy_str)

if __name__ == "__main__":
    unittest.main()
