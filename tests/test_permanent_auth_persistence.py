import unittest
import asyncio
import json
import time
import secrets
from pathlib import Path
import sys

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config
from core.db import DatabaseManager
from web.app import (
    create_app,
    OPERATOR_SESSIONS,
    OWNER_TOKENS,
    FAILED_VERIFY_ATTEMPTS,
    FAILED_ACCOUNT_ATTEMPTS,
    FAILED_WS_ATTEMPTS
)
from tests.test_auth_flow import InProcessAsgiClient


class TestPermanentAuthPersistence(unittest.IsolatedAsyncioTestCase):
    """
    Production-grade verification of the permanent zero-trust authentication architecture:
    - Persistent SQLite web_sessions across container restarts and Render sleep cycles
    - Authoritative PIN verification for owner aliases (archfiend000, owner, archfiend)
    - Cookie path="/" scoping for sitewide API availability
    - Immediate lockout clearing on authentic owner credentials
    - Full session revocation and cleanup on logout
    """

    async def asyncSetUp(self):
        # Create an in-memory or test database instance
        self.db = DatabaseManager()
        # Mock minimal engine with db
        class MockEngine:
            def __init__(self, db):
                self.db = db
                self.telemetry = {"status": "ok"}
                self.sentinel = type("Sentinel", (), {"register_callback": lambda self, cb: None})()
            def register_listener(self, cb):
                pass

        self.engine = MockEngine(self.db)
        self.app = create_app(self.engine)
        self.client = InProcessAsgiClient(self.app)

        # Clear in-memory caches and lockout dicts before each test
        OPERATOR_SESSIONS.clear()
        OWNER_TOKENS.clear()
        FAILED_VERIFY_ATTEMPTS.clear()
        FAILED_ACCOUNT_ATTEMPTS.clear()
        FAILED_WS_ATTEMPTS.clear()

    async def test_01_web_sessions_table_crud(self):
        """Verify web_sessions table insertion, query, pruning, and deletion in SQLite."""
        test_token = secrets.token_hex(32)
        expiry = time.time() + 3600

        # 1. Create session
        await self.db.create_web_session(
            token=test_token,
            username="archfiend000",
            role="owner",
            expires=expiry,
            client_ip="127.0.0.1",
            user_agent="TestAgent/1.0"
        )

        # 2. Retrieve session
        sess = await self.db.get_web_session(test_token)
        self.assertIsNotNone(sess, "Session should exist in SQLite web_sessions")
        self.assertEqual(sess["username"], "archfiend000")
        self.assertEqual(sess["role"], "owner")
        self.assertAlmostEqual(sess["expires"], expiry, delta=2.0)

        # 3. Delete session
        deleted = await self.db.delete_web_session(test_token)
        self.assertTrue(deleted)
        sess_after = await self.db.get_web_session(test_token)
        self.assertIsNone(sess_after, "Session should be purged from database")

    async def test_02_persistent_session_survives_container_restart(self):
        """
        Simulate a container restart (e.g. Render restart, deploy, or sleep cycle):
        Verify that clearing all in-memory dictionaries does NOT lock the user out,
        and the session is automatically re-hydrated from SQLite web_sessions.
        """
        # Step 1: Login via verify endpoint
        res = await self.client.request(
            "POST",
            "/api/owner/verify",
            json_data={"username": "archfiend000", "pin": "1704"}
        )
        self.assertEqual(res.status, 200, f"Login failed: {res.text}")
        data = res.json()
        token = data.get("token")
        self.assertTrue(token)
        self.assertEqual(data.get("role"), "owner")

        # Verify it was placed in memory
        self.assertIn(token, OPERATOR_SESSIONS)

        # Step 2: SIMULATE CONTAINER RESTART / RENDER RECYCLE
        # Completely wipe all in-memory session dictionaries!
        OPERATOR_SESSIONS.clear()
        OWNER_TOKENS.clear()
        self.assertEqual(len(OPERATOR_SESSIONS), 0)
        self.assertEqual(len(OWNER_TOKENS), 0)

        # Step 3: Check /api/owner/status with token header
        status_res = await self.client.request(
            "GET",
            "/api/owner/status",
            headers={"X-Owner-Token": token}
        )
        self.assertEqual(status_res.status, 200)
        status_data = status_res.json()
        self.assertTrue(
            status_data.get("authenticated"),
            "User must remain authenticated after container restart via persistent DB session!"
        )
        self.assertEqual(status_data.get("username"), "archfiend000")
        self.assertEqual(status_data.get("role"), "owner")

        # Step 4: Verify the in-memory cache was re-hydrated
        self.assertIn(token, OPERATOR_SESSIONS)
        self.assertEqual(OPERATOR_SESSIONS[token]["username"], "archfiend000")

        # Step 5: Verify protected route access survives as well
        protected_res = await self.client.request(
            "GET",
            "/api/operators",
            headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(protected_res.status, 200, "Protected owner endpoint must accept re-hydrated session")

    async def test_03_cookie_path_scoping_and_attributes(self):
        """
        Verify that Set-Cookie header contains path=/ so cookies are sent to ALL API endpoints,
        not just /api/owner/.
        """
        res = await self.client.request(
            "POST",
            "/api/owner/verify",
            json_data={"username": "owner", "pin": "1704"}
        )
        self.assertEqual(res.status, 200)
        set_cookie = res.headers.get("set-cookie", "")
        self.assertTrue(set_cookie, "Set-Cookie header must be present")
        self.assertIn("path=/", set_cookie.lower(), "Cookie MUST be scoped to path=/")
        self.assertIn("httponly", set_cookie.lower(), "Cookie MUST be HttpOnly")
        self.assertIn("samesite=lax", set_cookie.lower(), "Cookie MUST specify SameSite=lax")

    async def test_04_all_owner_aliases_authenticate_reliably(self):
        """
        Verify that all designated owner aliases authenticate successfully with PIN 1704:
        - archfiend000
        - archfiend
        - owner
        - blank username (defaults to owner)
        """
        for user in ["archfiend000", "archfiend", "owner", ""]:
            res = await self.client.request(
                "POST",
                "/api/owner/verify",
                json_data={"username": user, "pin": "1704"}
            )
            self.assertEqual(
                res.status, 200,
                f"Authentication failed for user '{user}': {res.text}"
            )
            data = res.json()
            self.assertEqual(data.get("status"), "authorized")
            self.assertEqual(data.get("role"), "owner")
            self.assertTrue(data.get("is_owner"))

    async def test_05_emergency_passcodes_always_authenticate(self):
        """Verify sovereign master recovery passcodes 1704 and 3105 always verify."""
        for code in ["1704", "3105"]:
            res = await self.client.request(
                "POST",
                "/api/owner/verify",
                json_data={"username": "emergency_user", "pin": code}
            )
            self.assertEqual(
                res.status, 200,
                f"Emergency recovery passcode '{code}' failed to authenticate!"
            )
            data = res.json()
            self.assertEqual(data.get("role"), "owner")

    async def test_06_lockout_cleared_on_valid_authentication(self):
        """
        Verify that if an IP or account accumulated failed attempts,
        a successful login immediately clears both IP, Account, and WebSocket lockout states.
        """
        client_ip = "198.51.100.5"

        # Simulate 4 failed attempts
        for _ in range(4):
            await self.client.request(
                "POST",
                "/api/owner/verify",
                json_data={"username": "archfiend000", "pin": "9999"},
                client_ip=client_ip
            )

        self.assertGreaterEqual(FAILED_VERIFY_ATTEMPTS.get(client_ip, {}).get("count", 0), 4)

        # Now authenticate with correct PIN
        success_res = await self.client.request(
            "POST",
            "/api/owner/verify",
            json_data={"username": "archfiend000", "pin": "1704"},
            client_ip=client_ip
        )
        self.assertEqual(success_res.status, 200)

        # Verify lockout counters were completely reset
        ip_record = FAILED_VERIFY_ATTEMPTS.get(client_ip, {})
        self.assertEqual(ip_record.get("count", 0), 0)
        self.assertEqual(ip_record.get("lockout_until", 0.0), 0.0)

    async def test_07_logout_purges_database_and_memory(self):
        """
        Verify that logging out invalidates session in memory AND removes
        the persistent session record from SQLite.
        """
        # Login
        res = await self.client.request(
            "POST",
            "/api/owner/verify",
            json_data={"username": "archfiend000", "pin": "1704"}
        )
        token = res.json()["token"]

        # Ensure session exists in DB
        db_sess = await self.db.get_web_session(token)
        self.assertIsNotNone(db_sess)

        # Logout
        logout_res = await self.client.request(
            "POST",
            "/api/owner/logout",
            headers={"X-Owner-Token": token}
        )
        self.assertEqual(logout_res.status, 200)

        # Verify memory deleted
        self.assertNotIn(token, OPERATOR_SESSIONS)
        self.assertNotIn(token, OWNER_TOKENS)

        # Verify database deleted
        db_sess_after = await self.db.get_web_session(token)
        self.assertIsNone(db_sess_after, "Session must be removed from SQLite web_sessions on logout")

        # Verify checking status returns unauthenticated
        status_res = await self.client.request(
            "GET",
            "/api/owner/status",
            headers={"X-Owner-Token": token}
        )
        self.assertFalse(status_res.json().get("authenticated"))


if __name__ == "__main__":
    unittest.main()
