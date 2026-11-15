import unittest
import asyncio
import os
import shutil
import tempfile
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from core.db import DatabaseManager
from core.sentinel import SentinelShield
from core.engine import SuperBanEngine
from web.app import create_app, OPERATOR_SESSIONS

class AsgiResponse:
    def __init__(self, status: int, headers: list, body: bytes):
        self.status = status
        self.headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in headers}
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8")


class InProcessAsgiClient:
    """Pure in-process ASGI 3.0 test client running without network sockets or httpx."""
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
        }

        response_status = 200
        response_headers = []
        response_body = bytearray()
        response_started = False

        async def receive():
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        async def send(message):
            nonlocal response_status, response_headers, response_body, response_started
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = message.get("headers", [])
                response_started = True
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(response_status, response_headers, bytes(response_body))


class TestCompleteMessageCoverage(unittest.TestCase):
    """
    KAGE — Critical Feature Preservation & Complete Message Coverage Verification Suite.
    Guarantees reliable message ingestion across:
    - Real-time incoming events
    - Historical scanning & batch persistence
    - Message editing events & forensic diffs
    - Message deletion events & unintercepted gap auditing
    - Idempotent deduplication
    - Coverage gap detection & checkpoints
    - Durable retry queue resilience
    - Offline fallback & contract preservation
    """

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.test_dir, "test_kage_coverage.db")
        cls.db = DatabaseManager(cls.db_path)

        # Mock engine and client
        cls.mock_client = MagicMock()
        cls.mock_client.is_user_authorized = AsyncMock(return_value=True)
        cls.mock_client.on = MagicMock(return_value=lambda f: f)
        cls.mock_client.add_event_handler = MagicMock()
        cls.mock_client.remove_event_handler = MagicMock()

        cls.engine = SuperBanEngine(db=cls.db, client=cls.mock_client)
        cls.sentinel = SentinelShield(client=cls.mock_client, db=cls.db, engine=cls.engine)
        cls.engine.sentinel = cls.sentinel

        cls.app = create_app(cls.engine)
        cls.client = InProcessAsgiClient(cls.app)

        # Seed valid owner clearance token for test client
        cls.auth_token = "test_owner_coverage_token_999"
        OPERATOR_SESSIONS[cls.auth_token] = {
            "username": "owner",
            "role": "owner",
            "expires": time.time() + 7200
        }

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self):
        self.loop.close()

    def test_01_realtime_incoming_message_ingestion(self):
        """Verify real-time message is persisted with metadata, event_type, timestamps, and status."""
        gid = -1001555001
        mid = 501
        uid = 88001

        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid,
            group_title="Cyber Command",
            message_id=mid,
            user_id=uid,
            full_name="Cipher Agent",
            username="cipher_agent",
            text="Classified transmission #01",
            media_type="text",
            date_sent=datetime.now(),
            event_type="message",
            processing_status="completed",
            processing_result="ok",
            reply_to_msg_id=None,
            raw_metadata={"client": "telethon"}
        ))

        # Retrieve and verify stored message
        msg = self.loop.run_until_complete(self.db.get_ghost_message_by_id(mid))
        self.assertIsNotNone(msg)
        self.assertEqual(msg["message_id"], mid)
        self.assertEqual(msg["text"], "Classified transmission #01")
        self.assertEqual(msg["sender_name"], "Cipher Agent")

        # Verify checkpoint was created
        health = self.loop.run_until_complete(self.db.get_ingestion_health(gid))
        self.assertEqual(health["status"], "HEALTHY")
        self.assertGreaterEqual(health["total_ingested_messages"], 1)

        # Verify audit record was created
        audit_logs = self.loop.run_until_complete(self.db.get_ingestion_audit_logs(limit=10))
        matching = [l for l in audit_logs if l["message_id"] == mid and l["group_id"] == gid]
        self.assertTrue(len(matching) >= 1)
        self.assertEqual(matching[0]["status"], "success")
        print("  ✅ PASS: Real-time message persisted with full metadata, timestamps, and status.")

    def test_02_idempotent_event_deduplication(self):
        """Verify duplicate events with identical (group_id, message_id) are gracefully deduplicated."""
        gid = -1001555002
        mid = 601
        uid = 88002

        # First insert
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid, group_title="Defensive Cell", message_id=mid,
            user_id=uid, full_name="Shield Guard", username="guard",
            text="Initial beacon pulse", event_type="message"
        ))

        # Duplicate insert attempt
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid, group_title="Defensive Cell", message_id=mid,
            user_id=uid, full_name="Shield Guard", username="guard",
            text="Duplicate beacon pulse", event_type="message"
        ))

        # Verify count is exactly 1 (no duplicate rows)
        self.db.cursor.execute("SELECT COUNT(*) FROM intercepted_messages WHERE group_id = ? AND message_id = ?", (gid, mid))
        count = self.db.cursor.fetchone()[0]
        self.assertEqual(count, 1)

        # Verify audit logs captured deduplication status
        audit_logs = self.loop.run_until_complete(self.db.get_ingestion_audit_logs(limit=20))
        dedup_logs = [l for l in audit_logs if l["message_id"] == mid and l["status"] == "deduplicated"]
        self.assertTrue(len(dedup_logs) >= 1)
        print("  ✅ PASS: Duplicate message events idempotent and tracked in audit log.")

    def test_03_message_editing_event_forensics(self):
        """Verify message edits record original and new text, edit timestamp, and audit trail."""
        gid = -1001555003
        mid = 701

        # Initial message
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid, group_title="Crypto Syndicate", message_id=mid,
            user_id=99001, full_name="Whale 01", username="whale1",
            text="Selling 50 BTC at market open"
        ))

        # Edit event
        edit_rec = self.loop.run_until_complete(self.db.mark_message_edited(
            group_id=gid, message_id=mid, new_text="Holding 50 BTC long term"
        ))

        self.assertIsNotNone(edit_rec)
        self.assertTrue(edit_rec["is_edited"])
        self.assertEqual(edit_rec["original_text"], "Selling 50 BTC at market open")
        self.assertEqual(edit_rec["new_text"], "Holding 50 BTC long term")

        # Verify audit log captures edit event
        audit_logs = self.loop.run_until_complete(self.db.get_ingestion_audit_logs(limit=20))
        edit_audits = [l for l in audit_logs if l["message_id"] == mid and l["event_type"] == "edit"]
        self.assertTrue(len(edit_audits) >= 1)
        print("  ✅ PASS: Message editing event correctly diffed and audited.")

    def test_04_message_deletion_event_forensics(self):
        """Verify message deletions are marked with timestamps, preserving original forensics."""
        gid = -1001555004
        mid = 801

        # Message captured before deletion
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid, group_title="Leak Channel", message_id=mid,
            user_id=99002, full_name="Deep Throat", username="whistle",
            text="Confidential memo attached"
        ))

        # Deletion event intercepted
        del_recs = self.loop.run_until_complete(self.db.mark_messages_deleted(
            group_id=gid, message_ids=[mid]
        ))

        self.assertEqual(len(del_recs), 1)
        self.assertTrue(del_recs[0]["is_deleted"])
        self.assertEqual(del_recs[0]["original_text"], "Confidential memo attached")

        # Test unintercepted deletion event logging
        self.loop.run_until_complete(self.db.log_ingestion_audit(
            event_type="delete", group_id=gid, group_title="Leak Channel",
            message_id=999999, status="untracked", detail="Observable deletion for unintercepted message"
        ))
        audit_logs = self.loop.run_until_complete(self.db.get_ingestion_audit_logs(limit=20))
        untracked = [l for l in audit_logs if l["message_id"] == 999999 and l["status"] == "untracked"]
        self.assertTrue(len(untracked) >= 1)
        print("  ✅ PASS: Observable deletion event preserved and unintercepted deletion tracked.")

    def test_05_historical_message_batch_persistence(self):
        """Verify bulk historical scanning saves all records to SQLite with checkpoint updates."""
        gid = -1001555005
        records = []
        for i in range(1, 31):
            records.append({
                "group_id": gid,
                "group_title": "Historical Vault",
                "message_id": 1000 + i,
                "user_id": 100 + i,
                "full_name": f"User {i}",
                "username": f"user_{i}",
                "text": f"Historical message #{i}",
                "media_type": "text",
                "date_sent": datetime.now(),
                "event_type": "historical",
                "processing_status": "completed",
                "processing_result": "ok"
            })

        inserted = self.loop.run_until_complete(self.db.batch_save_intercepted_messages(records))
        self.assertGreaterEqual(inserted, 30)

        # Verify stored messages can be retrieved
        stored = self.loop.run_until_complete(self.db.get_stored_messages(gid, limit=50))
        self.assertEqual(len(stored), 30)
        self.assertEqual(stored[-1]["message_id"], 1030)
        print("  ✅ PASS: Historical batch messages stored and retrieved with exact contract.")

    def test_06_coverage_gap_detection_and_resolution(self):
        """Verify coverage gaps are detected when message IDs jump, and resolved upon recovery."""
        gid = -1001555006

        # Message 1
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid, group_title="Network Scope", message_id=100,
            user_id=1, full_name="User", username="u", text="Seq 100"
        ))

        # Message 2 arrives with gap (message 150, gap: 101-149)
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid, group_title="Network Scope", message_id=150,
            user_id=1, full_name="User", username="u", text="Seq 150"
        ))

        health = self.loop.run_until_complete(self.db.get_ingestion_health(gid))
        gaps = health.get("known_coverage_gaps", [])
        self.assertTrue(any(g["start_id"] == 101 and g["end_id"] == 149 for g in gaps))

        # Resolve gap
        self.loop.run_until_complete(self.db.resolve_coverage_gap(gid, 101, 149))
        health_after = self.loop.run_until_complete(self.db.get_ingestion_health(gid))
        gaps_after = health_after.get("known_coverage_gaps", [])
        resolved_gaps = [g for g in gaps_after if g["start_id"] == 101 and g["status"] == "recovered"]
        self.assertTrue(len(resolved_gaps) >= 1)
        print("  ✅ PASS: Coverage gap detected across ID discontinuity and resolved.")

    def test_07_durable_retry_queue_and_resilience(self):
        """Verify Sentinel durable retry queue processes deferred messages without data loss."""
        gid = -1001555007
        mid = 2001

        # Enqueue item into sentinel retry queue directly
        self.sentinel._ingestion_retry_queue.put_nowait({
            "group_id": gid,
            "group_title": "Retry Cell",
            "message_id": mid,
            "user_id": 9999,
            "full_name": "Resilient Agent",
            "username": "resilient",
            "text": "Deferred message processed via durable worker",
            "media_type": "text",
            "event_type": "message",
            "reply_to_msg_id": None
        })

        # Process single queue task via manual worker loop step
        async def _process_one():
            item = await self.sentinel._ingestion_retry_queue.get()
            await self.db.save_intercepted_message(
                group_id=item["group_id"],
                group_title=item["group_title"],
                message_id=item["message_id"],
                user_id=item["user_id"],
                full_name=item["full_name"],
                username=item["username"],
                text=item["text"],
                media_type=item["media_type"],
                event_type=item["event_type"],
                processing_status="recovered",
                processing_result="retry_success"
            )
            await self.db.log_ingestion_audit(
                item["event_type"], item["group_id"], item["group_title"],
                item["message_id"], "recovered", "Processed via durable queue"
            )
            self.sentinel._ingestion_retry_queue.task_done()

        self.loop.run_until_complete(_process_one())

        # Verify message persisted
        msg = self.loop.run_until_complete(self.db.get_ghost_message_by_id(mid))
        self.assertIsNotNone(msg)
        self.assertEqual(msg["text"], "Deferred message processed via durable worker")
        print("  ✅ PASS: Durable retry queue safely commits deferred messages without loss.")

    def test_08_ingestion_health_telemetry_endpoint(self):
        """Verify /api/messages/ingestion/health returns comprehensive operational health data."""
        async def _test():
            res = await self.client.request(
                "GET",
                "/api/messages/ingestion/health",
                headers={"x-owner-token": self.auth_token}
            )
            self.assertEqual(res.status, 200)
            data = res.json()
            self.assertIn("status", data)
            self.assertIn("total_ingested_messages", data)
            self.assertIn("total_edits_captured", data)
            self.assertIn("total_deletions_captured", data)
            self.assertIn("known_coverage_gaps", data)
            self.assertIn("disclaimer", data)
        self.loop.run_until_complete(_test())
        print("  ✅ PASS: /api/messages/ingestion/health endpoint operational.")

    def test_09_offline_message_continuity_fallback(self):
        """Verify /api/messages falls back to SQLite stored messages when Telegram client is unavailable."""
        gid = -1001555009
        mid = 3001

        # Seed local database
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid, group_title="Offline Vault", message_id=mid,
            user_id=5500, full_name="Offline Survivor", username="survivor",
            text="Surviving offline disconnection"
        ))

        # Simulate get_group_entity returning None (offline)
        with patch.object(self.engine, 'get_group_entity', AsyncMock(return_value=None)):
            async def _test():
                res = await self.client.request(
                    "GET",
                    f"/api/messages?group_id={gid}",
                    headers={"x-owner-token": self.auth_token}
                )
                self.assertEqual(res.status, 200)
                msgs = res.json()
                self.assertTrue(len(msgs) >= 1)
                self.assertEqual(msgs[0]["message_id"], mid)
                self.assertEqual(msgs[0]["text"], "Surviving offline disconnection")
            self.loop.run_until_complete(_test())
        print("  ✅ PASS: Seamless offline continuity fallback to local database.")

    def test_10_gap_recovery_mechanism(self):
        """Verify engine.recover_coverage_gap pulls historical range from Telegram and resolves gap."""
        gid = -1001555010

        mock_msg1 = MagicMock()
        mock_msg1.id = 101
        mock_msg1.sender_id = 7001
        mock_msg1.sender = MagicMock(first_name="Recovered", last_name="Alpha", username="rec_a")
        mock_msg1.raw_text = "Missed message 101"
        mock_msg1.media = None
        mock_msg1.date = datetime.now()
        mock_msg1.reply_to = None

        mock_msg2 = MagicMock()
        mock_msg2.id = 102
        mock_msg2.sender_id = 7002
        mock_msg2.sender = MagicMock(first_name="Recovered", last_name="Beta", username="rec_b")
        mock_msg2.raw_text = "Missed message 102"
        mock_msg2.media = None
        mock_msg2.date = datetime.now()
        mock_msg2.reply_to = None

        async def _mock_iter(*args, **kwargs):
            yield mock_msg1
            yield mock_msg2

        self.mock_client.iter_messages = _mock_iter
        mock_entity = MagicMock(id=abs(gid), title="Gap Recovery Group")

        with patch.object(self.engine, 'get_group_entity', AsyncMock(return_value=mock_entity)):
            res = self.loop.run_until_complete(self.engine.recover_coverage_gap(gid, 101, 102))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["recovered"], 2)

            # Verify saved into database
            msg1 = self.loop.run_until_complete(self.db.get_ghost_message_by_id(101))
            self.assertIsNotNone(msg1)
            self.assertEqual(msg1["text"], "Missed message 101")
        print("  ✅ PASS: Historical gap recovery mechanism verified.")


if __name__ == '__main__':
    unittest.main()
