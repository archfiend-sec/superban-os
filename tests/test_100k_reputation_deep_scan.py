import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.db import DatabaseManager
from core.forensics import ForensicInspector
from core.engine import SuperBanEngine
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


class Test100kReputationEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create temporary test DB
        cls.temp_db_fd, cls.temp_db_path = tempfile.mkstemp(suffix=".sqlite3")
        cls.db = DatabaseManager(db_path=cls.temp_db_path)

        # Mock telethon client
        cls.mock_client = MagicMock()
        cls.engine = SuperBanEngine(cls.mock_client, cls.db)
        cls.app = create_app(cls.engine)
        cls.client = InProcessAsgiClient(cls.app)

        # Valid operator token
        cls.token = "test_100k_operator_token_999"
        OPERATOR_SESSIONS[cls.token] = {"username": "archfiend000", "role": "owner", "expires": 9999999999.0}

    @classmethod
    def tearDownClass(cls):
        try:
            cls.db.close()
            os.close(cls.temp_db_fd)
            if os.path.exists(cls.temp_db_path):
                os.remove(cls.temp_db_path)
        except Exception:
            pass

    def test_01_smra_100k_scoring_algorithm(self):
        """Verify Sovereign Multi-Factor Reputation Algorithm (SMRA-100K) calculations."""
        # 1. Veteran with 1,500 messages, 45 days tenure, reactions, verified
        vet_stats = {
            "message_count": 1500,
            "days_active": 45,
            "reactions_received": 120,
            "replies_received": 40,
            "is_verified": True,
            "is_premium": True,
            "has_photo": True,
            "is_bot": False,
            "risk_flags": []
        }
        res_vet = ForensicInspector.calculate_reputation_score(vet_stats)
        self.assertGreaterEqual(res_vet["score"], 90)
        self.assertIn(res_vet["tier"], ["LEGENDARY PILLAR", "SOVEREIGN VERIFIED"])
        self.assertEqual(res_vet["color"], "#10b981") # emerald

        # 2. Regular active member with 120 messages, 8 days tenure
        reg_stats = {
            "message_count": 120,
            "days_active": 8,
            "reactions_received": 15,
            "replies_received": 5,
            "is_verified": False,
            "is_premium": False,
            "has_photo": True,
            "is_bot": False,
            "risk_flags": []
        }
        res_reg = ForensicInspector.calculate_reputation_score(reg_stats)
        self.assertGreaterEqual(res_reg["score"], 70)
        self.assertLess(res_reg["score"], 90)
        self.assertIn(res_reg["tier"], ["CORE CONTRIBUTOR", "SOVEREIGN VERIFIED", "REGULAR CITIZEN"])

        # 3. Lurker with 1 message, joined yesterday
        lurker_stats = {
            "message_count": 1,
            "days_active": 1,
            "reactions_received": 0,
            "replies_received": 0,
            "is_verified": False,
            "is_premium": False,
            "has_photo": True,
            "is_bot": False,
            "risk_flags": []
        }
        res_lurker = ForensicInspector.calculate_reputation_score(lurker_stats)
        self.assertLessEqual(res_lurker["score"], 65)

        # 4. Spammer / Throwaway account with flags
        spam_stats = {
            "message_count": 5,
            "days_active": 0,
            "reactions_received": 0,
            "replies_received": 0,
            "is_verified": False,
            "is_premium": False,
            "has_photo": False,
            "is_bot": False,
            "risk_flags": ["NEW_ACCOUNT_THROWAWAY", "SPAM_SCORE_ELEVATED"]
        }
        res_spam = ForensicInspector.calculate_reputation_score(spam_stats)
        self.assertLessEqual(res_spam["score"], 40)
        self.assertIn(res_spam["tier"], ["SUSPICIOUS TRANSIENT", "CRITICAL RISK"])
        print("  ✅ PASS: SMRA-100K scoring accurately differentiates veterans, regulars, lurkers, and spammers.")

    def test_02_database_batch_upsert_and_retrieval(self):
        """Verify database batch upserting, individual fetching, and multi-lookup."""
        async def run_test():
            group_id = 100998877
            members = [
                {
                    "group_id": group_id,
                    "user_id": 1001,
                    "username": "veteran_alice",
                    "full_name": "Alice Veteran",
                    "message_count": 2500,
                    "media_count": 300,
                    "reactions_received": 150,
                    "replies_received": 60,
                    "first_seen": "2026-08-01 10:00:00",
                    "last_seen": "2026-10-25 18:00:00",
                    "avg_message_len": 42.5,
                    "reputation_score": 96,
                    "reputation_tier": "LEGENDARY OPERATIVE",
                    "reputation_color": "#10b981",
                    "risk_flags": [],
                    "is_premium": True,
                    "is_verified": True,
                    "has_photo": True,
                    "is_bot": False
                },
                {
                    "group_id": group_id,
                    "user_id": 1002,
                    "username": "resident_bob",
                    "full_name": "Bob Resident",
                    "message_count": 180,
                    "media_count": 20,
                    "reactions_received": 18,
                    "replies_received": 10,
                    "first_seen": "2026-10-15 12:00:00",
                    "last_seen": "2026-10-25 17:30:00",
                    "avg_message_len": 35.0,
                    "reputation_score": 78,
                    "reputation_tier": "TRUSTED RESIDENT",
                    "reputation_color": "#38bdf8",
                    "risk_flags": [],
                    "is_premium": False,
                    "is_verified": False,
                    "has_photo": True,
                    "is_bot": False
                },
                {
                    "group_id": group_id,
                    "user_id": 1003,
                    "username": "shady_eve",
                    "full_name": "Eve Unknown",
                    "message_count": 2,
                    "media_count": 0,
                    "reactions_received": 0,
                    "replies_received": 0,
                    "first_seen": "2026-10-25 15:00:00",
                    "last_seen": "2026-10-25 15:05:00",
                    "avg_message_len": 12.0,
                    "reputation_score": 38,
                    "reputation_tier": "ELEVATED RISK",
                    "reputation_color": "#f59e0b",
                    "risk_flags": ["SUSPICIOUS_JOIN"],
                    "is_premium": False,
                    "is_verified": False,
                    "has_photo": False,
                    "is_bot": False
                }
            ]

            # Upsert
            await self.db.batch_upsert_member_reputation(members)

            # Single member fetch
            alice = await self.db.get_group_member_reputation(group_id, 1001)
            self.assertIsNotNone(alice)
            self.assertEqual(alice["user_id"], 1001)
            self.assertEqual(alice["username"], "veteran_alice")
            self.assertEqual(alice["message_count"], 2500)
            self.assertEqual(alice["reputation_score"], 96)
            self.assertEqual(alice["reputation_tier"], "LEGENDARY OPERATIVE")

            # Multiple member lookup
            rep_map = await self.db.get_multiple_member_reputations(group_id, [1001, 1002, 1003, 9999])
            self.assertEqual(len(rep_map), 3)
            self.assertEqual(rep_map[1001]["reputation_score"], 96)
            self.assertEqual(rep_map[1002]["reputation_score"], 78)
            self.assertEqual(rep_map[1003]["reputation_score"], 38)
            self.assertNotIn(9999, rep_map)

            # Ranked list sorting by reputation
            ranked_by_rep = await self.db.get_group_members_ranked(group_id, sort_by="reputation")
            self.assertEqual(len(ranked_by_rep), 3)
            self.assertEqual(ranked_by_rep[0]["user_id"], 1001)
            self.assertEqual(ranked_by_rep[1]["user_id"], 1002)
            self.assertEqual(ranked_by_rep[2]["user_id"], 1003)

            # Filtering by top_chatters
            top_chatters = await self.db.get_group_members_ranked(group_id, filter_mode="top_chatters")
            self.assertEqual(len(top_chatters), 2)
            self.assertNotIn(1003, [m["user_id"] for m in top_chatters])

            # Summary aggregation
            summary = await self.db.get_group_reputation_summary(group_id)
            self.assertEqual(summary["total_members_profiled"], 3)
            self.assertEqual(summary["total_messages_indexed"], 2682)
            self.assertGreater(summary["avg_reputation_score"], 60)
            self.assertEqual(len(summary["top_chatters"]), 3)

        asyncio.run(run_test())
        print("  ✅ PASS: Database batch upsert, ranking, multi-lookup, and summary metrics verified.")

    def test_03_engine_deep_scan_streaming(self):
        """Verify engine deep_scan_group_messages with simulated message stream."""
        async def run_test():
            group_id = 200554433
            group_mock = MagicMock(id=group_id, title="War Room Alpha")

            # Create 150 simulated messages
            fake_messages = []
            now = datetime.now()
            for i in range(150):
                msg = MagicMock()
                msg.id = 1000 + i
                sender_uid = 2001 if i < 100 else (2002 if i < 140 else 2003)
                msg.sender_id = sender_uid
                msg.date = now - timedelta(minutes=(150 - i) * 10)
                msg.text = f"Simulated intel broadcast message #{i} with key data points."
                msg.media = MagicMock() if (i % 5 == 0) else None
                msg.reactions = MagicMock(results=[MagicMock(count=1)]) if (i % 10 == 0) else None
                msg.reply_to = MagicMock(reply_to_msg_id=999) if (i % 7 == 0) else None

                sender = MagicMock()
                sender.id = sender_uid
                sender.username = f"agent_{sender_uid}"
                sender.first_name = f"Agent {sender_uid}"
                sender.last_name = ""
                sender.premium = (sender_uid == 2001)
                sender.verified = False
                sender.photo = MagicMock()
                sender.bot = False
                sender.deleted = False
                sender.scam = False
                sender.fake = False
                msg.sender = sender
                msg.get_sender = AsyncMock(return_value=sender)
                fake_messages.append(msg)

            async def mock_iter_messages(entity, limit=100000):
                for m in fake_messages:
                    yield m

            self.mock_client.iter_messages = mock_iter_messages

            # Execute scan
            result = await self.engine.deep_scan_group_messages(group_mock, limit=150)
            self.assertEqual(result["scanned"], 150)
            self.assertEqual(result["unique_members"], 3)
            self.assertEqual(result["status"].upper(), "COMPLETED")

            # Check status cache
            status = await self.engine.get_deep_scan_status(group_id)
            self.assertEqual(status["status"].upper(), "COMPLETED")
            self.assertEqual(status["scanned"], 150)

            # Check DB was populated
            ranked = await self.db.get_group_members_ranked(group_id)
            self.assertEqual(len(ranked), 3)
            # Agent 2001 should have 100 messages and highest score
            agent_2001 = next(m for m in ranked if m["user_id"] == 2001)
            self.assertEqual(agent_2001["message_count"], 100)
            self.assertGreaterEqual(agent_2001["reputation_score"], 70)

        asyncio.run(run_test())
        print("  ✅ PASS: Engine 100k streaming scan processes messages, aggregates stats, and commits to SQLite.")

    def test_04_deep_scan_cancel_mechanism(self):
        """Verify deep scan can be aborted cleanly via cancel_deep_scan."""
        async def run_test():
            group_id = 300998811
            group_mock = MagicMock(id=group_id, title="Test Abort Group")

            async def slow_iter_messages(entity, limit=100000):
                for i in range(100):
                    msg = MagicMock()
                    msg.id = i
                    msg.sender_id = 999
                    msg.date = datetime.now()
                    msg.text = "test"
                    msg.media = None
                    msg.reactions = None
                    msg.reply_to = None
                    sender = MagicMock(id=999, username="test", first_name="Test", last_name="", premium=False, verified=False, photo=None, bot=False)
                    msg.sender = sender
                    msg.get_sender = AsyncMock(return_value=sender)
                    yield msg
                    await asyncio.sleep(0.02)

            self.mock_client.iter_messages = slow_iter_messages

            # Launch scan in background task
            scan_task = asyncio.create_task(self.engine.deep_scan_group_messages(group_mock, limit=100))
            await asyncio.sleep(0.05)

            # Cancel scan
            res = await self.engine.cancel_deep_scan(group_id)
            self.assertTrue(res["cancelled"])

            # Await task completion
            result = await scan_task
            self.assertEqual(result["status"].upper(), "CANCELLED")

            status = await self.engine.get_deep_scan_status(group_id)
            self.assertEqual(status["status"].upper(), "CANCELLED")

        asyncio.run(run_test())
        print("  ✅ PASS: Deep scan cancellation cleanly halts streaming ingestion without leaks.")

    def test_05_rest_api_deep_scan_and_reputation_grid(self):
        """Verify REST endpoints /api/messages/deep_scan, /status, /cancel, /api/members/reputation, /api/members/summary."""
        async def run_test():
            headers = {"Authorization": f"Bearer {self.token}", "X-Owner-Token": self.token}
            group_id = 100998877 # Populated in test_02

            # 1. Fetch ranked members list
            res = await self.client.request(
                "GET",
                f"/api/members/reputation?group_id={group_id}&sort_by=reputation&filter_mode=all",
                headers=headers
            )
            self.assertEqual(res.status, 200)
            data = res.json()
            self.assertEqual(len(data), 3)
            self.assertEqual(data[0]["username"], "veteran_alice")

            # 2. Fetch summary metrics
            res_sum = await self.client.request(
                "GET",
                f"/api/members/summary?group_id={group_id}",
                headers=headers
            )
            self.assertEqual(res_sum.status, 200)
            sum_data = res_sum.json()
            self.assertEqual(sum_data["total_members_profiled"], 3)
            self.assertEqual(sum_data["total_messages_indexed"], 2682)

            # 3. Check /api/osint/user enriched with group reputation
            self.engine.osint_inspector = MagicMock()
            self.engine.osint_inspector.inspect_user = AsyncMock(return_value={
                "user_id": 1001,
                "first_name": "Alice",
                "username": "veteran_alice",
                "trust_score": 75,
                "trust_level": "NEUTRAL"
            })

            res_osint = await self.client.request(
                "GET",
                f"/api/osint/user?user_id=1001&group_id={group_id}",
                headers=headers
            )
            self.assertEqual(res_osint.status, 200)
            osint_data = res_osint.json()
            self.assertIn("group_reputation", osint_data)
            self.assertEqual(osint_data["group_reputation"]["message_count"], 2500)
            self.assertEqual(osint_data["group_reputation"]["reputation_score"], 96)
            self.assertEqual(osint_data["trust_score"], 96)
            self.assertEqual(osint_data["trust_level"], "LEGENDARY OPERATIVE")

            # 4. Deep scan trigger endpoint
            self.engine.get_group_entity = AsyncMock(return_value=MagicMock(id=group_id, title="Test Group"))
            res_scan = await self.client.request(
                "POST",
                "/api/messages/deep_scan",
                headers=headers,
                json_data={"group_id": group_id, "limit": 10000}
            )
            self.assertEqual(res_scan.status, 200)
            scan_payload = res_scan.json()
            self.assertIn(scan_payload["status"], ["started", "scan_started"])

            # 5. Check status endpoint
            res_status = await self.client.request(
                "GET",
                f"/api/messages/deep_scan/status?group_id={group_id}",
                headers=headers
            )
            self.assertEqual(res_status.status, 200)

            # 6. Cancel scan endpoint
            res_cancel = await self.client.request(
                "POST",
                "/api/messages/deep_scan/cancel",
                headers=headers,
                json_data={"group_id": group_id}
            )
            self.assertEqual(res_cancel.status, 200)
            self.assertEqual(res_cancel.json()["status"], "scan_cancelled")

        asyncio.run(run_test())
        print("  ✅ PASS: All REST endpoints for 100k scan, reputation grid, and OSINT enrichment verified.")

    def test_06_wiretap_reputation_intelligence_injection(self):
        """Verify get_recent_messages injects 100k reputation score, tier, and message count badges."""
        async def run_test():
            group_id = 100998877

            # Create mock message from Alice (1001)
            msg1 = MagicMock()
            msg1.id = 5501
            msg1.sender_id = 1001
            msg1.text = "Strategic directive initialized."
            msg1.media = None
            msg1.sticker = False
            msg1.gif = False
            msg1.video_note = False
            msg1.video = False
            msg1.photo = False
            msg1.voice = False
            msg1.audio = False
            msg1.date = MagicMock()
            msg1.date.strftime.return_value = "18:00:00"
            msg1.date.timestamp.return_value = 1700000000.0
            msg1.get_sender = AsyncMock(return_value=MagicMock(id=1001, first_name="Alice", last_name="Veteran", username="veteran_alice"))

            # Create mock message from Eve (1003)
            msg2 = MagicMock()
            msg2.id = 5502
            msg2.sender_id = 1003
            msg2.text = "Hello is anyone here?"
            msg2.media = None
            msg2.sticker = False
            msg2.gif = False
            msg2.video_note = False
            msg2.video = False
            msg2.photo = False
            msg2.voice = False
            msg2.audio = False
            msg2.date = MagicMock()
            msg2.date.strftime.return_value = "18:01:00"
            msg2.date.timestamp.return_value = 1700000060.0
            msg2.get_sender = AsyncMock(return_value=MagicMock(id=1003, first_name="Eve", last_name="", username="shady_eve"))

            self.mock_client.get_messages = AsyncMock(return_value=[msg2, msg1])

            wiretap_cards = await self.engine.get_recent_messages(MagicMock(id=group_id), limit=10)
            self.assertEqual(len(wiretap_cards), 2)

            # Check Alice card
            alice_card = next(c for c in wiretap_cards if c["user_id"] == 1001)
            self.assertEqual(alice_card["trust_score"], 96)
            self.assertEqual(alice_card["trust_level"], "LEGENDARY OPERATIVE")
            self.assertEqual(alice_card["trust_color"], "#10b981")
            self.assertEqual(alice_card["message_count"], 2500)

            # Check Eve card
            eve_card = next(c for c in wiretap_cards if c["user_id"] == 1003)
            self.assertEqual(eve_card["trust_score"], 38)
            self.assertEqual(eve_card["trust_level"], "ELEVATED RISK")
            self.assertEqual(eve_card["trust_color"], "#f59e0b")
            self.assertEqual(eve_card["message_count"], 2)

        asyncio.run(run_test())
        print("  ✅ PASS: Wiretap message cards successfully enriched with 100k reputation badges & message counts.")

    def test_07_social_graph_and_ai_profile_integration(self):
        """Verify 100k message scan powers social graph topology and AI profiling."""
        async def run_test():
            group_id = 99887766

            # 1. Test AI persona archetype inference
            archetype = ForensicInspector.infer_persona_archetype(
                message_count=600,
                days_active=25,
                avg_len=45,
                media_count=30,
                replies_received=40,
                reactions_received=50
            )
            self.assertIn("COMMUNITY ANCHOR", archetype["archetype"])

            # 2. Test batch recording social interaction edges from 100k scan
            interactions = [
                (group_id, 3001, "Alice Alpha", 3002, "Bob Beta", "reply", 9901, "2026-11-08 12:00:00"),
                (group_id, 3001, "Alice Alpha", 3002, "Bob Beta", "reply", 9902, "2026-11-08 12:05:00"),
                (group_id, 3003, "Charlie Call", 3001, "Alice Alpha", "mention", 9903, "2026-11-08 12:10:00")
            ]
            await self.db.batch_record_social_interactions(interactions)

            # Query social graph
            graph = await self.db.get_social_graph(group_id=group_id)
            self.assertEqual(graph["edge_count"], 2)
            self.assertEqual(graph["node_count"], 3)
            # Top influencer should be Alice or Bob with degree 3
            top_node = graph["top_influencers"][0]
            self.assertIn(top_node["id"], [3001, 3002])

            # 3. Test batch user cross profile updating from 100k scan
            members = [
                {
                    "user_id": 3001,
                    "full_name": "Alice Alpha",
                    "username": "alice_alpha",
                    "message_count": 850,
                    "is_premium": True,
                    "is_verified": False
                }
            ]
            await self.db.batch_update_user_cross_profiles(group_id, "Test War Room", members)
            cross_profile = await self.db.get_user_cross_profile(3001)
            self.assertIsNotNone(cross_profile)
            self.assertEqual(cross_profile["message_count"], 850)
            self.assertEqual(cross_profile["current_name"], "Alice Alpha")
            self.assertTrue(cross_profile["is_premium"])

            # 4. Test sleep cycle calculation fallback from 100k scan hourly distribution
            hourly_test = [0] * 24
            for h in range(8, 22):
                hourly_test[h] = 20  # Active between 08:00 and 22:00 UTC (Sleep between 22:00 and 07:00 UTC)
            
            # Save into group_member_reputation
            await self.db.batch_upsert_member_reputation([
                {
                    "group_id": group_id,
                    "user_id": 3001,
                    "username": "alice_alpha",
                    "full_name": "Alice Alpha",
                    "message_count": 850,
                    "media_count": 50,
                    "reactions_received": 100,
                    "replies_received": 50,
                    "avg_message_len": 55.0,
                    "reputation_score": 92,
                    "reputation_tier": "LEGENDARY OPERATIVE",
                    "reputation_color": "#10b981",
                    "risk_flags": [],
                    "hourly_activity": hourly_test,
                    "persona_archetype": "COMMUNITY ANCHOR // 柱石",
                    "top_associates": [{"user_id": 3002, "name": "Bob Beta", "interaction_count": 2}]
                }
            ])

            sleep_profile = await self.db.get_user_sleep_cycle_profile(3001)
            self.assertEqual(sleep_profile["total_messages"], sum(hourly_test))
            self.assertNotEqual(sleep_profile["sleep_window_utc"], "Insufficient telemetry")
            self.assertIn("UTC", sleep_profile["estimated_timezone"])

        asyncio.run(run_test())
        print("  ✅ PASS: 100k deep scan properly powers social graph topology, AI persona archetypes, and sleep cycles.")

    def test_08_polymorphic_group_id_matching(self):
        """Verify seamless cross-format group ID resolution (-100 prefix vs bare ID) preventing Wiretap reputation drops."""
        async def run_test():
            canonical_gid = 1890044567
            telethon_chat_id = -1001890044567
            sonu_uid = 7370260179

            # 1. Batch upsert SONU under canonical group ID (as saved by deep scan)
            sonu_record = {
                "group_id": canonical_gid,
                "user_id": sonu_uid,
                "username": "sonu_official",
                "full_name": "SONU",
                "message_count": 3100,
                "media_count": 240,
                "reactions_received": 1500,
                "replies_received": 450,
                "avg_message_len": 42.0,
                "reputation_score": 98,
                "reputation_tier": "LEGENDARY COMMANDER // 伝説",
                "reputation_color": "#38B98C",
                "risk_flags": [],
                "is_premium": False,
                "is_verified": False,
                "has_photo": True,
                "is_bot": False,
                "hourly_activity": [10] * 24,
                "persona_archetype": "LEADERSHIP ANCHOR // 指導",
                "top_associates": []
            }
            await self.db.batch_upsert_member_reputation(canonical_gid, [sonu_record])

            # 2. Query with negative Telethon event.chat_id (-1001890044567)
            rep = await self.db.get_group_member_reputation(telethon_chat_id, sonu_uid)
            self.assertIsNotNone(rep, "Failed to resolve member reputation using Telethon -100... chat_id!")
            self.assertEqual(rep["message_count"], 3100)
            self.assertEqual(rep["reputation_score"], 98)
            self.assertEqual(rep["reputation_tier"], "LEGENDARY COMMANDER // 伝説")

            # 3. Batch lookup with negative chat_id
            multi_rep = await self.db.get_multiple_member_reputations(telethon_chat_id, [sonu_uid])
            self.assertIn(sonu_uid, multi_rep)
            self.assertEqual(multi_rep[sonu_uid]["message_count"], 3100)
            self.assertEqual(multi_rep[sonu_uid]["reputation_score"], 98)

            # 4. Ranked members lookup with negative chat_id
            ranked = await self.db.get_group_members_ranked(telethon_chat_id)
            self.assertTrue(any(m["user_id"] == sonu_uid for m in ranked))

            # 5. Reputation summary with negative chat_id
            summary = await self.db.get_group_reputation_summary(telethon_chat_id)
            self.assertGreaterEqual(summary["total_messages_indexed"], 3100)

            # 6. Verify ForensicInspector correctly keeps SONU at 98% LEGENDARY during live message
            mock_sender = MagicMock()
            mock_sender.id = sonu_uid
            mock_sender.first_name = "SONU"
            mock_sender.last_name = ""
            mock_sender.username = "sonu_official"
            mock_sender.bot = False
            mock_sender.deleted = False
            mock_sender.verified = False
            mock_sender.scam = False
            mock_sender.fake = False
            mock_sender.premium = False
            mock_sender.photo = MagicMock()
            mock_sender.status = None

            profile = ForensicInspector.inspect_user(mock_sender, member_stats=rep)
            self.assertEqual(profile["trust_score"], 98)
            self.assertIn("LEGENDARY", profile["trust_level"])
            self.assertNotEqual(profile["trust_score"], 38, "Discrepancy reproduced: SONU fell back to 38%!")

        asyncio.run(run_test())
        print("  ✅ PASS: Polymorphic group ID matching prevents reputation drop and resolves -100 prefix discrepancies.")

    def test_09_saved_chat_database_cloud_sync_and_restore(self):
        """Verify userbot Saved Messages ('me') cloud vault synchronization, database export, and instant restoration."""
        async def run_test():
            mock_client = MagicMock()
            mock_client.is_connected = MagicMock(return_value=True)
            mock_client.get_me = AsyncMock(return_value=MagicMock(id=88889999, username="archfiend000"))
            
            engine = SuperBanEngine(mock_client, self.db)
            canonical_gid = 9876543210
            
            # Seed members
            member_data = [
                {
                    "user_id": 9001,
                    "username": "commander_valkyrie",
                    "full_name": "Commander Valkyrie",
                    "message_count": 1420,
                    "reputation_score": 96,
                    "reputation_tier": "LEGENDARY COMMANDER",
                    "reputation_color": "#10b981",
                    "persona_archetype": "LEADERSHIP ANCHOR // 指導",
                    "top_associates": []
                },
                {
                    "user_id": 9002,
                    "username": "shadow_recon",
                    "full_name": "Shadow Recon",
                    "message_count": 890,
                    "reputation_score": 88,
                    "reputation_tier": "ELITE VETERAN",
                    "reputation_color": "#38bdf8",
                    "persona_archetype": "TACTICAL SCOUT // 偵察",
                    "top_associates": []
                }
            ]
            await self.db.batch_upsert_member_reputation(canonical_gid, member_data)

            # Mock Telethon client
            mock_client = MagicMock()
            engine.client = mock_client
            engine.clients = [mock_client]
            engine.is_connected = True
            engine.ensure_connected = AsyncMock()

            # Capture sent file payload
            sent_payload_holder = {}
            async def fake_send_file(chat, file=None, caption="", parse_mode="html"):
                self.assertEqual(chat, 'me')
                self.assertIn("#KAGE_INTEL_DB", caption)
                self.assertIn(f"#GROUP_{canonical_gid}", caption)
                # Read payload from temp file before it's deleted
                with open(file, "r", encoding="utf-8") as f:
                    sent_payload_holder["json"] = json.load(f)
                fake_msg = MagicMock()
                fake_msg.id = 55512
                return fake_msg

            mock_client.send_file = AsyncMock(side_effect=fake_send_file)

            # 1. Sync to Saved Chat
            sync_res = await engine.sync_deep_scan_to_saved_chat(canonical_gid, note="Automated Test Backup")
            self.assertTrue(sync_res["success"])
            self.assertEqual(sync_res["total_members"], 2)
            self.assertIn("json", sent_payload_holder)
            self.assertEqual(sent_payload_holder["json"]["schema"], "KAGE_SOVEREIGN_INTEL_DB")

            # 2. Simulate restoring from Saved Messages into a fresh group ID
            restore_gid = 1122334455
            # Setup fake iter_messages on 'me'
            saved_doc = MagicMock()
            saved_doc.message = f"Executive briefing #KAGE_INTEL_DB #GROUP_{restore_gid}"
            saved_doc.file = MagicMock()
            saved_doc.file.name = f"KAGE_INTEL_GROUP_{restore_gid}.json"
            saved_doc.media = MagicMock()

            # Payload adapted for restore_gid
            restore_payload = dict(sent_payload_holder["json"])
            restore_payload["group_id"] = restore_gid

            async def fake_iter_messages(chat, search="", limit=20):
                if chat == 'me':
                    yield saved_doc

            mock_client.iter_messages = fake_iter_messages
            mock_client.download_media = AsyncMock(return_value=json.dumps(restore_payload).encode('utf-8'))

            restore_res = await engine.restore_deep_scan_from_saved_chat(restore_gid)
            self.assertTrue(restore_res["success"])
            self.assertEqual(restore_res["restored_members"], 2)

            # Verify restored records exist in SQLite under restore_gid
            rep9001 = await self.db.get_group_member_reputation(restore_gid, 9001)
            self.assertIsNotNone(rep9001)
            self.assertEqual(rep9001["message_count"], 1420)
            self.assertEqual(rep9001["reputation_score"], 96)

            # 3. Test REST Endpoints via ASGI Client
            app = create_app(engine)
            asgi_client = InProcessAsgiClient(app)
            
            # Auth cookie
            token = "test-saved-chat-token"
            OPERATOR_SESSIONS[token] = {
                "role": "owner",
                "username": "archfiend000",
                "expires": time.time() + 86400
            }
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Owner-Token": token,
                "Cookie": f"session_token={token}"
            }

            resp_sync = await asgi_client.request(
                "POST", "/api/saved_chat/sync",
                headers=headers,
                json_data={"group_id": canonical_gid}
            )
            self.assertEqual(resp_sync.status, 200)
            self.assertTrue(resp_sync.json()["success"])

            resp_restore = await asgi_client.request(
                "POST", "/api/saved_chat/restore",
                headers=headers,
                json_data={"group_id": restore_gid}
            )
            self.assertEqual(resp_restore.status, 200)
            self.assertTrue(resp_restore.json()["success"])

        asyncio.run(run_test())
        print("  ✅ PASS: Saved Chat ('me') decentralized cloud database backup & restore fully operational.")


if __name__ == "__main__":
    print("=" * 75)
    print("⚡ 100,000 MESSAGE DEEP SCAN & REPUTATION ENGINE // TEST SUITE")
    print("=" * 75)
    unittest.main()
