"""
TACTICAL SURVEILLANCE RADAR VERIFICATION SUITE // PRODUCTION-GRADE VALIDATION
Comprehensive validation of the KAGE Surveillance Radar Subsystem:
1. Watchlist Rule Creation, Normalization, & Input Validation (Keyword vs User)
2. Duplicate Rule Rejection & Multi-State Toggle/Lifecycle (Active vs Paused)
3. Real-Time Alert Logging & Strict Message-Level Idempotency (Zero Duplicate Alerts)
4. Safe Identity Resolution & Zero 'User undefined' Fallbacks
5. Multi-Criteria Alert Querying, Filtering, & Pagination
6. Alert Acknowledgement & Clean Purge Protocols
7. Sync Engine Execution, Telemetry Persistence, & DB State Transitions
8. Zero-Trust Authentication Enforcement on Surveillance API Endpoints
"""

import unittest
import asyncio
import tempfile
import time
import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.db import DatabaseManager
from core.engine import SuperBanEngine
from core.sentinel import SentinelShield
from core.config import Config
from web.app import create_app, OPERATOR_SESSIONS

class AsgiResponse:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8")

class InProcessAsgiClient:
    """Pure in-process ASGI 3.0 test client running without network sockets."""
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

        raw_headers = [
            (k.encode("latin-1"), v.encode("latin-1"))
            for k, v in req_headers.items()
        ]

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("latin-1"),
            "query_string": query_string.encode("latin-1"),
            "headers": raw_headers,
            "client": (client_ip, 54321),
            "server": ("testserver", 80),
        }

        response_status = 500
        response_headers = {}
        response_body = []

        async def receive():
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        async def send(message):
            nonlocal response_status, response_headers, response_body
            if message["type"] == "http.response.start":
                response_status = message["status"]
                for k, v in message.get("headers", []):
                    response_headers[k.decode("latin-1").lower()] = v.decode("latin-1")
            elif message["type"] == "http.response.body":
                response_body.append(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(response_status, response_headers, b"".join(response_body))


class TestSurveillanceRadar(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_surveillance.db"
        self.db = DatabaseManager(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tmp_dir.cleanup()

    def test_01_watchlist_validation_and_duplicate_prevention(self):
        """Test rule creation, target type handling, normalization, and duplicate rejection."""
        # 1. Valid Keyword
        rule = asyncio.run(self.db.add_watchlist_item("keyword", "Airdrop Claim", "Crypto scam keyword"))
        self.assertIsNotNone(rule)
        rule_id = rule["id"]
        self.assertGreater(rule_id, 0)
        self.assertEqual(rule["value"], "airdrop claim")

        # 2. Duplicate Keyword Rejection (Case-Insensitive)
        with self.assertRaises(ValueError):
            asyncio.run(self.db.add_watchlist_item("keyword", "airdrop claim", "Duplicate"))

        # 3. Valid Numeric User ID
        user_rule = asyncio.run(self.db.add_watchlist_item("user", "987654321", "Target scammer ID"))
        self.assertIsNotNone(user_rule)
        self.assertEqual(user_rule["value"], "987654321")

        # 4. Valid Username with normalization
        handle_rule = asyncio.run(self.db.add_watchlist_item("user", "@ScamOperator", "Target handle"))
        handle_rule_id = handle_rule["id"]
        rules = asyncio.run(self.db.get_watchlist(include_inactive=True))
        target_rule = next(r for r in rules if r["id"] == handle_rule_id)
        self.assertEqual(target_rule["value"], "scamoperator")

        # 5. Duplicate User Rejection
        with self.assertRaises(ValueError):
            asyncio.run(self.db.add_watchlist_item("user", "@scamoperator", "Duplicate handle"))

        # 6. Malformed User Target Rejection
        with self.assertRaises(ValueError):
            asyncio.run(self.db.add_watchlist_item("user", "not_valid_user!@#", "Invalid format"))

        # 7. Unsupported Target Type Rejection
        with self.assertRaises(ValueError):
            asyncio.run(self.db.add_watchlist_item("unsupported_type", "test", "Invalid type"))

        print("  ✅ PASS: Watchlist input validation, normalization & duplicate rejection verified.")

    def test_02_watchlist_lifecycle_and_toggle(self):
        """Test active/pause toggling, updates, and deletion of surveillance rules."""
        rule = asyncio.run(self.db.add_watchlist_item("keyword", "drainer", "Wallet drainer trigger"))
        rule_id = rule["id"]
        
        # Verify initial active status
        active_rules = asyncio.run(self.db.get_watchlist(include_inactive=False))
        self.assertTrue(any(r["id"] == rule_id for r in active_rules))

        # Toggle to Paused
        toggled = asyncio.run(self.db.toggle_watchlist_item(rule_id))
        self.assertEqual(toggled["is_active"], 0)

        # Verify not returned when include_inactive=False
        active_only = asyncio.run(self.db.get_watchlist(include_inactive=False))
        self.assertFalse(any(r["id"] == rule_id for r in active_only))

        # Verify returned when include_inactive=True
        all_rules = asyncio.run(self.db.get_watchlist(include_inactive=True))
        toggled_rule = next(r for r in all_rules if r["id"] == rule_id)
        self.assertEqual(toggled_rule["is_active"], 0)

        # Toggle back to Active
        activated = asyncio.run(self.db.toggle_watchlist_item(rule_id))
        self.assertEqual(activated["is_active"], 1)

        # Update note
        updated = asyncio.run(self.db.update_watchlist_item(rule_id, note="Updated threat intel note"))
        self.assertTrue(updated)

        # Delete rule
        deleted = asyncio.run(self.db.delete_watchlist_item(rule_id))
        self.assertTrue(deleted)
        remaining = asyncio.run(self.db.get_watchlist(include_inactive=True))
        self.assertFalse(any(r["id"] == rule_id for r in remaining))

        print("  ✅ PASS: Watchlist rule lifecycle, pause/activate toggling & deletion verified.")

    def test_03_alert_logging_idempotency_and_safe_identity(self):
        """Verify alert logging idempotency, safe fallbacks, and zero 'User undefined'."""
        rule = asyncio.run(self.db.add_watchlist_item("keyword", "seed phrase", "Security leak trigger"))
        rule_id = rule["id"]

        # 1. Log valid alert
        alert_id_1 = asyncio.run(self.db.log_surveillance_alert(
            watchlist_id=rule_id,
            target_type="keyword",
            trigger_val="seed phrase",
            group_id=-100123456789,
            group_title="Alpha Trading Hub",
            message_id=4501,
            user_id=777888999,
            full_name="Cipher Shiller",
            username="ciphershil",
            message_text="Enter your seed phrase here for instant bonus!"
        ))
        self.assertIsNotNone(alert_id_1)

        # 2. Strict Idempotency Check: Log duplicate alert for SAME (watchlist_id, group_id, message_id)
        alert_id_dup = asyncio.run(self.db.log_surveillance_alert(
            watchlist_id=rule_id,
            target_type="keyword",
            trigger_val="seed phrase",
            group_id=-100123456789,
            group_title="Alpha Trading Hub",
            message_id=4501,
            user_id=777888999,
            full_name="Cipher Shiller",
            username="ciphershil",
            message_text="Enter your seed phrase here for instant bonus!"
        ))
        self.assertEqual(alert_id_1, alert_id_dup, "Duplicate message intercept must return existing alert ID without creating duplicate row")

        # 3. Log alert with MISSING / NULL identity fields (Testing safe fallback)
        alert_id_anon = asyncio.run(self.db.log_surveillance_alert(
            watchlist_id=rule_id,
            target_type="keyword",
            trigger_val="seed phrase",
            group_id=-100123456789,
            group_title="",
            message_id=4502,
            user_id=None,
            full_name="",
            username=None,
            message_text=""
        ))
        self.assertIsNotNone(alert_id_anon)

        # Query and verify no "User undefined" or null crashes
        res = asyncio.run(self.db.get_surveillance_alerts(limit=10))
        anon_alert = next(a for a in res["alerts"] if a["id"] == alert_id_anon)
        self.assertEqual(anon_alert["full_name"], "Unknown User")
        self.assertEqual(anon_alert["user_id"], 0)
        self.assertEqual(anon_alert["username"], "")
        self.assertNotIn("undefined", anon_alert["full_name"].lower())

        print("  ✅ PASS: Alert logging idempotency & safe identity resolution verified.")

    def test_04_alert_filtering_pagination_and_acknowledgement(self):
        """Test multi-criteria search, pagination, and alert acknowledgment."""
        rule_kw = asyncio.run(self.db.add_watchlist_item("keyword", "urgent", "Urgency trigger"))
        rule_kw_id = rule_kw["id"]
        rule_usr = asyncio.run(self.db.add_watchlist_item("user", "111222333", "Target operator"))
        rule_usr_id = rule_usr["id"]

        # Create multiple alerts
        for i in range(1, 16):
            asyncio.run(self.db.log_surveillance_alert(
                watchlist_id=rule_kw_id if i % 2 == 1 else rule_usr_id,
                target_type="keyword" if i % 2 == 1 else "user",
                trigger_val="urgent" if i % 2 == 1 else "111222333",
                group_id=-100111222,
                group_title="Group Alpha",
                message_id=1000 + i,
                user_id=5000 + i,
                full_name=f"Participant {i}",
                username=f"user_{i}",
                message_text=f"This is an urgent announcement #{i}"
            ))

        # 1. Total and Pagination Check
        page1 = asyncio.run(self.db.get_surveillance_alerts(limit=5, offset=0))
        self.assertEqual(page1["total"], 15)
        self.assertEqual(len(page1["alerts"]), 5)

        page2 = asyncio.run(self.db.get_surveillance_alerts(limit=5, offset=5))
        self.assertEqual(len(page2["alerts"]), 5)
        self.assertNotEqual(page1["alerts"][0]["id"], page2["alerts"][0]["id"])

        # 2. Filter by Rule ID
        rule_res = asyncio.run(self.db.get_surveillance_alerts(rule_id=rule_usr_id))
        self.assertTrue(all(a["watchlist_id"] == rule_usr_id for a in rule_res["alerts"]))

        # 3. Filter by Keyword
        kw_res = asyncio.run(self.db.get_surveillance_alerts(keyword="Participant 7"))
        self.assertEqual(len(kw_res["alerts"]), 1)
        self.assertEqual(kw_res["alerts"][0]["full_name"], "Participant 7")

        # 4. Acknowledge single alert
        target_id = page1["alerts"][0]["id"]
        ack_res = asyncio.run(self.db.acknowledge_surveillance_alert(target_id))
        self.assertTrue(ack_res)

        unack_res = asyncio.run(self.db.get_surveillance_alerts(unack_only=True))
        self.assertFalse(any(a["id"] == target_id for a in unack_res["alerts"]))

        # 5. Acknowledge all alerts
        ack_all_count = asyncio.run(self.db.acknowledge_all_surveillance_alerts())
        self.assertGreaterEqual(ack_all_count, 14)
        unack_empty = asyncio.run(self.db.get_surveillance_alerts(unack_only=True))
        self.assertEqual(len(unack_empty["alerts"]), 0)

        # 6. Clear all alerts
        cleared = asyncio.run(self.db.clear_surveillance_alerts())
        self.assertEqual(cleared, 15)
        final_check = asyncio.run(self.db.get_surveillance_alerts())
        self.assertEqual(final_check["total"], 0)

        print("  ✅ PASS: Alert filtering, pagination, acknowledgement & purge verified.")

    def test_05_sync_state_persistence(self):
        """Verify surveillance sync state persistence across engine cycles."""
        initial_state = asyncio.run(self.db.get_surveillance_sync_state())
        self.assertEqual(initial_state["status"], "IDLE")

        # Update sync state to SYNCING
        asyncio.run(self.db.update_surveillance_sync_state(status="SYNCING"))
        mid_state = asyncio.run(self.db.get_surveillance_sync_state())
        self.assertEqual(mid_state["status"], "SYNCING")

        # Complete sync with execution metrics
        asyncio.run(self.db.update_surveillance_sync_state(
            status="COMPLETED",
            rules_checked=8,
            messages_scanned=240,
            alerts_generated=3,
            error_detail=None
        ))
        final_state = asyncio.run(self.db.get_surveillance_sync_state())
        self.assertEqual(final_state["status"], "COMPLETED")
        self.assertEqual(final_state["rules_checked"], 8)
        self.assertEqual(final_state["messages_scanned"], 240)
        self.assertEqual(final_state["alerts_generated"], 3)
        self.assertIsNotNone(final_state["last_sync_time"])

        print("  ✅ PASS: Surveillance sync state persistence & execution metrics verified.")

    def test_06_sentinel_realtime_cross_examination(self):
        """Test Sentinel real-time message cross-examination with active and inactive rules."""
        async def run_test():
            # Create active and inactive rules
            active_kw = await self.db.add_watchlist_item("keyword", "phishing", "Active scam keyword")
            paused_kw = await self.db.add_watchlist_item("keyword", "bonus", "Paused trigger")
            await self.db.toggle_watchlist_item(paused_kw["id"]) # Pause it
            user_rule = await self.db.add_watchlist_item("user", "999000111", "Active threat actor")

            # Setup mock sentinel
            mock_client = MagicMock()
            mock_engine = MagicMock()
            mock_engine.db = self.db

            sentinel = SentinelShield(mock_client, self.db, mock_engine)
            events_emitted = []

            async def mock_emit(event_type, payload):
                events_emitted.append((event_type, payload))

            sentinel.emit_sentinel_event = mock_emit

            # 1. Message matching active keyword -> MUST generate alert
            await sentinel._check_surveillance(
                text="Warning: this is a phishing link http://fake-login.com",
                user_id=12345,
                full_name="Alice Smith",
                username="alicesmith",
                group_id=-100999,
                group_title="Test Chat",
                msg_id=701
            )
            self.assertEqual(len(events_emitted), 1)
            self.assertEqual(events_emitted[0][0], "surveillance_target_hit")
            self.assertEqual(events_emitted[0][1]["trigger_val"], "phishing")

            # 2. Message matching PAUSED keyword -> MUST NOT generate alert
            await sentinel._check_surveillance(
                text="Claim your free bonus now!",
                user_id=12345,
                full_name="Alice Smith",
                username="alicesmith",
                group_id=-100999,
                group_title="Test Chat",
                msg_id=702
            )
            self.assertEqual(len(events_emitted), 1, "Paused rule must not trigger alert")

            # 3. Message from target user -> MUST generate alert
            await sentinel._check_surveillance(
                text="Hello everyone, just joining.",
                user_id=999000111,
                full_name="Threat Actor",
                username="threat_actor",
                group_id=-100999,
                group_title="Test Chat",
                msg_id=703
            )
            self.assertEqual(len(events_emitted), 2)
            self.assertEqual(events_emitted[1][1]["target_type"], "user")
            self.assertEqual(events_emitted[1][1]["trigger_val"], "999000111")

        asyncio.run(run_test())
        print("  ✅ PASS: Sentinel real-time cross-examination & rule status filtering verified.")

    def test_07_rest_api_security_and_endpoints(self):
        """Verify server-side authentication, REST API contract, and input security."""
        mock_engine = MagicMock()
        mock_engine.db = self.db
        mock_engine.client = MagicMock()
        mock_engine.client.is_connected = MagicMock(return_value=True)
        mock_engine.get_authorized_groups = AsyncMock(return_value=[{"id": -100111, "title": "Secured Group"}])
        mock_engine.sync_surveillance_radar = AsyncMock()
        mock_engine.get_surveillance_sync_status = AsyncMock(return_value={
            "status": "IDLE",
            "last_sync_time": "2026-11-22 20:00:00",
            "rules_checked": 4,
            "messages_scanned": 100,
            "alerts_generated": 2
        })

        app = create_app(mock_engine)
        client = InProcessAsgiClient(app)

        # 1. Unauthenticated Request -> Must return 401
        unauth_res = asyncio.run(client.request("GET", "/api/surveillance/watchlist"))
        self.assertEqual(unauth_res.status, 401)

        # 2. Authenticated Session Setup
        session_token = "kage_test_surveillance_token"
        OPERATOR_SESSIONS[session_token] = {
            "username": "owner",
            "role": "owner",
            "expires": time.time() + 3600
        }
        auth_headers = {
            "Cookie": f"kage_session={session_token}",
            "x-owner-token": session_token
        }

        # 3. GET Watchlist (Empty)
        get_res = asyncio.run(client.request("GET", "/api/surveillance/watchlist", headers=auth_headers))
        self.assertEqual(get_res.status, 200)
        self.assertIsInstance(get_res.json(), list)

        # 4. POST Create Watchlist Rule
        post_res = asyncio.run(client.request(
            "POST",
            "/api/surveillance/watchlist",
            headers=auth_headers,
            json_data={"target_type": "keyword", "value": "hacked account", "note": "Account takeover"}
        ))
        self.assertEqual(post_res.status, 200)
        new_rule = post_res.json()
        self.assertIn("id", new_rule)
        rule_id = new_rule["id"]

        # 5. POST Toggle Rule
        toggle_res = asyncio.run(client.request("POST", f"/api/surveillance/watchlist/{rule_id}/toggle", headers=auth_headers))
        self.assertEqual(toggle_res.status, 200)
        self.assertFalse(toggle_res.json()["is_active"])

        # 6. GET Alerts
        alerts_res = asyncio.run(client.request("GET", "/api/surveillance/alerts", headers=auth_headers))
        self.assertEqual(alerts_res.status, 200)
        self.assertIn("alerts", alerts_res.json())

        # 7. POST Sync Trigger & Status
        sync_res = asyncio.run(client.request("POST", "/api/surveillance/sync", headers=auth_headers))
        self.assertEqual(sync_res.status, 200)

        status_res = asyncio.run(client.request("GET", "/api/surveillance/sync/status", headers=auth_headers))
        self.assertEqual(status_res.status, 200)
        self.assertIn("status", status_res.json())

        # Clean up session
        del OPERATOR_SESSIONS[session_token]
        print("  ✅ PASS: Surveillance REST API auth & full endpoint functionality verified.")


if __name__ == "__main__":
    unittest.main()
