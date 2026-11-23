"""
Verification script for KAGE Header Cleanup & Logout Restoration
Validates:
1. Header markup structure and presence of Logout across viewports
2. Absence of visible health clutter
3. Functionality and security of POST /api/owner/logout
4. Client-side state invalidation and post-logout 401 enforcement
"""
import unittest
import asyncio
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.db import DatabaseManager
from core.engine import SuperBanEngine
from core.config import Config
from web.app import create_app, OPERATOR_SESSIONS, OWNER_TOKENS
from tests.test_pentest_validation import InProcessAsgiClient

class TestHeaderCleanupAndLogout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html_path = Path("/Users/archfiend/.gemini/antigravity/scratch/telegram_group_manager/web/static/index.html")
        cls.html_content = cls.html_path.read_text(encoding="utf-8")

    def test_01_health_clutter_removed_from_visible_header_but_preserved_in_dom(self):
        """Visible status indicators TG: ONLINE, API: OK, JOBS, SYNC removed visually but preserved for polling."""
        # Check DOM element exists so pollSystemHealth() does not throw JS errors
        self.assertIn('id="kage-sys-health-compact"', self.html_content, "DOM element #kage-sys-health-compact must be preserved in DOM")
        self.assertIn('id="health-tg-status"', self.html_content, "DOM element #health-tg-status must exist")
        self.assertIn('id="health-backend-status"', self.html_content, "DOM element #health-backend-status must exist")
        self.assertIn('id="health-jobs-status"', self.html_content, "DOM element #health-jobs-status must exist")
        self.assertIn('id="health-sync-time"', self.html_content, "DOM element #health-sync-time must exist")

        # Check CSS enforces display: none !important
        css_match = re.search(r'#kage-sys-health-compact[^{]*\{[^}]*display:\s*none\s*!important', self.html_content)
        self.assertIsNotNone(css_match, "CSS must hide #kage-sys-health-compact with display: none !important")

    def test_02_logout_buttons_present_and_configured(self):
        """Logout button must be clearly defined in desktop header, mobile actions, tools dropdown, and mobile sheet."""
        # 1. Desktop header logout button
        self.assertIn('id="owner-logout-btn"', self.html_content)
        self.assertIn('confirmAndLogout()', self.html_content)
        self.assertIn('>LOGOUT<', self.html_content)

        # 2. Mobile header action logout button
        self.assertIn('id="mob-nav-logout-btn"', self.html_content)
        self.assertIn('class="kage-btn-pill mob-action-pill kage-logout-btn"', self.html_content)

        # 3. Tactical tools dropdown logout item
        self.assertIn('id="nav-tools-logout-btn"', self.html_content)

        # 4. Mobile tools sheet logout card
        self.assertIn('id="mob-logout-card"', self.html_content)

    def test_03_js_functions_implemented(self):
        """confirmAndLogout and updateLogoutButtonVisibility must be correctly defined."""
        self.assertIn("function confirmAndLogout()", self.html_content)
        self.assertIn("function updateLogoutButtonVisibility(", self.html_content)
        self.assertIn("showKageConfirm(", self.html_content)
        self.assertTrue("postlogout" in self.html_content.lower() or "/api/owner/logout" in self.html_content)

    def test_05_responsive_viewport_rules_and_touch_targets(self):
        """Verify CSS responsive media queries across mobile, tablet, and desktop viewports."""
        # 1. Mobile touch targets: .mob-action-pill must be at least 34px x 34px
        self.assertRegex(self.html_content, r'\.mob-action-pill\s*\{[^}]*width:\s*34px\s*!important')
        self.assertRegex(self.html_content, r'\.mob-action-pill\s*\{[^}]*height:\s*34px\s*!important')

        # 2. Tablet optimization rules: max-width 1180px and min-width 768px
        self.assertIn("@media (min-width: 768px) and (max-width: 1180px)", self.html_content)
        self.assertIn(".brand-tx i {", self.html_content)

        # 3. Mobile bottom dock strict hiding on monitor and tablet
        self.assertIn("body.device-tablet #kage-mobile-dock", self.html_content)
        self.assertIn("body.device-monitor #kage-mobile-dock", self.html_content)

        # 4. Universal health bar suppression
        self.assertRegex(self.html_content, r'\.kage-sys-health-bar,\s*#kage-sys-health-compact\s*\{[^}]*display:\s*none\s*!important')
        """Backend POST /api/owner/logout revokes session, deletes cookies, and prevents access to protected APIs."""
        async def run_async_test():
            temp_dir = tempfile.TemporaryDirectory()
            db_path = Path(temp_dir.name) / "test_logout.db"
            db = DatabaseManager(db_path)

            mock_client = AsyncMock()
            mock_client.is_connected = MagicMock(return_value=True)
            mock_client.on = MagicMock(return_value=lambda f: f)

            engine = SuperBanEngine(client=mock_client, db=db)
            app = create_app(engine)
            client = InProcessAsgiClient(app)

            # Step 1: Login as owner
            login_res = await client.request(
                "POST",
                "/api/owner/verify",
                json_data={"pin": "1704"}
            )
            self.assertEqual(login_res.status, 200, f"Login failed: {login_res.text}")
            login_data = login_res.json()
            token = login_data["token"]
            self.assertIn(token, OPERATOR_SESSIONS)
            self.assertIn(token, OWNER_TOKENS)

            # Check cookies were set
            set_cookie_header = login_res.headers.get("set-cookie", "")
            self.assertIn("owner_token=", set_cookie_header)

            # Step 2: Access a protected route with the token
            status_res = await client.request(
                "GET",
                "/api/owner/status",
                headers={"Authorization": f"Bearer {token}", "X-Owner-Token": token}
            )
            self.assertEqual(status_res.status, 200)
            self.assertTrue(status_res.json()["authenticated"])

            # Step 3: Perform Logout
            logout_res = await client.request(
                "POST",
                "/api/owner/logout",
                headers={"Authorization": f"Bearer {token}", "X-Owner-Token": token}
            )
            self.assertEqual(logout_res.status, 200)
            self.assertEqual(logout_res.json(), {"status": "logged_out"})

            # Verify cookies were deleted (max-age=0 or empty or expires in past)
            logout_set_cookie = logout_res.headers.get("set-cookie", "")
            self.assertIn('owner_token=""', logout_set_cookie)
            self.assertIn('kage_session=""', logout_set_cookie)

            # Step 4: Verify server-side invalidation
            self.assertNotIn(token, OPERATOR_SESSIONS, "Token must be evicted from OPERATOR_SESSIONS")
            self.assertNotIn(token, OWNER_TOKENS, "Token must be evicted from OWNER_TOKENS")

            # Verify DB session record deleted
            db_session = await db.get_web_session(token)
            self.assertIsNone(db_session, "Database web session must be deleted")

            # Step 5: Verify protected routes reject the invalidated token (HTTP 401)
            rejected_res = await client.request(
                "GET",
                "/api/owner/status",
                headers={"Authorization": f"Bearer {token}", "X-Owner-Token": token}
            )
            self.assertEqual(rejected_res.status, 200)
            self.assertFalse(rejected_res.json()["authenticated"], "Invalidated token must not be authenticated")

            # Operators management endpoint strictly enforces 401
            op_res = await client.request(
                "POST",
                "/api/operators/invite",
                headers={"Authorization": f"Bearer {token}", "X-Owner-Token": token},
                json_data={"username": "attacker", "passcode": "123456"}
            )
            self.assertEqual(op_res.status, 401, "Protected API must return 401 Unauthorized after logout")

            db.close()
            temp_dir.cleanup()

        asyncio.run(run_async_test())

if __name__ == "__main__":
    unittest.main()
