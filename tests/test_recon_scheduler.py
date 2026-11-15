import asyncio
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from core.db import DatabaseManager
from core.scheduler import (
    ReconScheduler,
    validate_schedule_time,
    validate_timezone,
    compute_next_run_utc,
    generate_slot_key,
    get_safe_zoneinfo,
)
from core.config import Config


class TestReconScheduler(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="kage_test_recon_")
        self.db_path = os.path.join(self.test_dir, "test_recon.db")
        self.db = DatabaseManager(self.db_path)
        self.db.init_schema()

        # Mock SuperBanEngine
        self.mock_engine = MagicMock()
        self.mock_engine.db = self.db
        self.mock_engine.normalize_gid = self.db.normalize_gid
        self.mock_engine.active_deep_scans = {}
        self.mock_engine.client = MagicMock()
        self.mock_engine.client.is_connected = MagicMock(return_value=True)
        self.mock_engine.client.is_user_authorized = AsyncMock(return_value=True)
        self.mock_engine.emit_telemetry = AsyncMock()
        self.mock_engine.get_group_entity = AsyncMock(return_value=MagicMock(id=123456789, title="Test Group Alpha"))
        self.mock_engine.deep_scan_group_messages = AsyncMock(return_value={
            "status": "completed",
            "messages_scanned": 1500,
            "unique_members": 85,
            "target_limit": 100000,
            "job_id": "test_recon_job"
        })

        self.scheduler = ReconScheduler(self.mock_engine, self.db, check_interval_sec=0.1)

    async def asyncTearDown(self):
        self.scheduler.stop()
        self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_validate_schedule_time(self):
        # Valid 24h times
        self.assertTrue(validate_schedule_time("04:00"))
        self.assertTrue(validate_schedule_time("00:00"))
        self.assertTrue(validate_schedule_time("23:59"))
        self.assertTrue(validate_schedule_time("12:30"))
        self.assertTrue(validate_schedule_time("4:00"))

        # Invalid times
        self.assertFalse(validate_schedule_time("24:00"))
        self.assertFalse(validate_schedule_time("12:60"))
        self.assertFalse(validate_schedule_time("invalid"))
        self.assertFalse(validate_schedule_time(""))
        self.assertFalse(validate_schedule_time(None))
        self.assertFalse(validate_schedule_time("04:00:00"))

    def test_validate_timezone(self):
        self.assertTrue(validate_timezone("UTC"))
        self.assertTrue(validate_timezone("America/New_York"))
        self.assertTrue(validate_timezone("Europe/London"))
        self.assertTrue(validate_timezone("Asia/Kolkata"))
        self.assertTrue(validate_timezone("Asia/Tokyo"))

        self.assertFalse(validate_timezone("Invalid/Timezone"))
        self.assertFalse(validate_timezone(""))
        self.assertFalse(validate_timezone(None))

    def test_compute_next_run_utc_advances_day(self):
        # If schedule is 04:00 UTC and current time is 05:00 UTC, next run must be tomorrow at 04:00 UTC
        base_utc = datetime(2026, 11, 16, 5, 0, 0, tzinfo=timezone.utc)
        next_run = compute_next_run_utc("04:00", "UTC", from_dt_utc=base_utc)
        self.assertEqual(next_run, datetime(2026, 11, 17, 4, 0, 0, tzinfo=timezone.utc))

        # If schedule is 06:00 UTC and current time is 05:00 UTC, next run must be today at 06:00 UTC
        next_run_today = compute_next_run_utc("06:00", "UTC", from_dt_utc=base_utc)
        self.assertEqual(next_run_today, datetime(2026, 11, 16, 6, 0, 0, tzinfo=timezone.utc))

    def test_compute_next_run_utc_across_timezones(self):
        # 04:00 in Asia/Kolkata (UTC+5:30) is 22:30 UTC of previous day
        base_utc = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
        next_run_ist = compute_next_run_utc("04:00", "Asia/Kolkata", from_dt_utc=base_utc)
        # Next 04:00 IST will be Nov 17 04:00 IST -> Nov 16 22:30 UTC
        self.assertEqual(next_run_ist, datetime(2026, 11, 16, 22, 30, 0, tzinfo=timezone.utc))

        # 04:00 in America/New_York (UTC-5 in standard time) is 09:00 UTC
        next_run_est = compute_next_run_utc("04:00", "America/New_York", from_dt_utc=base_utc)
        self.assertEqual(next_run_est, datetime(2026, 11, 17, 9, 0, 0, tzinfo=timezone.utc))

    def test_generate_slot_key(self):
        target_utc = datetime(2026, 11, 16, 4, 0, 0, tzinfo=timezone.utc)
        slot = generate_slot_key(100123456, target_utc, "04:00", "UTC")
        self.assertEqual(slot, "100123456:2026-11-16_04:00_UTC")

    async def test_database_schedule_crud_and_slot_locking(self):
        gid = 987654321
        # 1. Upsert schedule
        sched = await self.db.upsert_recon_schedule(
            group_id=gid,
            group_title="Test Alpha",
            is_enabled=True,
            schedule_time="04:00",
            timezone="UTC",
            target_limit=100000,
            next_run_at="2026-11-16T04:00:00+00:00"
        )
        self.assertIsNotNone(sched)
        self.assertEqual(sched["group_id"], gid)
        self.assertTrue(sched["is_enabled"])

        # 2. Get schedule
        fetched = await self.db.get_recon_schedule(gid)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["schedule_time"], "04:00")

        # 3. Acquire slot lock atomically
        slot_key = "987654321:2026-11-16_04:00_UTC"
        locked = await self.db.acquire_schedule_slot_lock(fetched["id"], slot_key)
        self.assertTrue(locked, "First slot acquisition must succeed")

        # 4. Duplicate slot acquisition attempt must FAIL
        dup_locked = await self.db.acquire_schedule_slot_lock(fetched["id"], slot_key)
        self.assertFalse(dup_locked, "Duplicate slot acquisition must be rejected")

    async def test_overlapping_scan_protection(self):
        gid = 123456789
        # Mark an active scan for this target in engine
        norm_gid = self.db.normalize_gid(gid)
        self.mock_engine.active_deep_scans[norm_gid] = {"job_id": "active_manual_job", "status": "PROCESSING"}

        schedule = {
            "id": 1,
            "group_id": gid,
            "group_title": "Active Group",
            "is_enabled": True,
            "schedule_time": "04:00",
            "timezone": "UTC",
            "target_limit": 100000,
            "next_run_at": "2026-11-16T04:00:00+00:00"
        }

        # Execute single schedule
        res = await self.scheduler.execute_single_schedule(schedule, execution_type="scheduled")
        self.assertEqual(res["status"].upper(), "SKIPPED")
        self.assertIn("Overlapping", res.get("reason", "") or res.get("detail", ""))

        # Verify execution record in database reflects SKIPPED
        history = await self.db.get_recon_schedule_executions(gid, limit=5)
        self.assertTrue(len(history) >= 1)
        self.assertEqual(history[0]["final_status"], "SKIPPED")
        self.assertIn("Overlapping", history[0]["error_details"])

    async def test_telegram_disconnected_handling(self):
        gid = 555666777
        # Disconnect client
        self.mock_engine.client.is_connected = MagicMock(return_value=False)

        schedule = {
            "id": 2,
            "group_id": gid,
            "group_title": "Offline Group",
            "is_enabled": True,
            "schedule_time": "04:00",
            "timezone": "UTC",
            "target_limit": 100000,
            "next_run_at": "2026-11-16T04:00:00+00:00"
        }

        res = await self.scheduler.execute_single_schedule(schedule, execution_type="scheduled")
        self.assertEqual(res["status"].upper(), "FAILED")
        self.assertIn("offline or unauthorized", res.get("error", "") or res.get("detail", ""))

        history = await self.db.get_recon_schedule_executions(gid, limit=5)
        self.assertTrue(len(history) >= 1)
        self.assertEqual(history[0]["final_status"], "FAILED")

    async def test_trigger_run_now_manual_execution(self):
        gid = 777888999
        await self.db.upsert_recon_schedule(
            group_id=gid,
            group_title="Run Now Target",
            is_enabled=True,
            schedule_time="04:00",
            timezone="UTC",
            target_limit=50000
        )

        res = await self.scheduler.trigger_run_now(gid)
        self.assertEqual(res["status"], "started")
        self.assertEqual(res["group_id"], self.db.normalize_gid(gid))

        # Allow execution task to complete
        await asyncio.sleep(0.15)

        history = await self.db.get_recon_schedule_executions(gid, limit=5)
        self.assertTrue(len(history) >= 1)
        self.assertIn("manual", history[0]["execution_type"])
        self.assertEqual(history[0]["final_status"], "COMPLETED")
        self.assertEqual(history[0]["messages_processed"], 1500)
        self.assertEqual(history[0]["unique_members_profiled"], 85)

    async def test_server_restart_auto_recovery(self):
        gid = 111222333
        now_iso = datetime.now().isoformat()
        # Insert a running execution into database as if server crashed mid-scan
        await self.db.create_recon_schedule_execution(
            execution_id="stalled_exec_test",
            schedule_id=99,
            group_id=gid,
            group_title="Stalled Target",
            scheduled_time=now_iso,
            actual_start_time=now_iso,
            execution_type="scheduled",
            final_status="RUNNING"
        )

        # Simulate server reboot by calling init_schema()
        self.db.init_schema()

        history = await self.db.get_recon_schedule_executions(gid, limit=5)
        self.assertTrue(len(history) >= 1)
        self.assertEqual(history[0]["final_status"], "FAILED")
        self.assertIn("Server restarted", history[0]["error_details"])

    async def test_social_graph_invisible_character_sanitization(self):
        # Verify that invisible unicode characters are stripped and don't produce blank node names
        gid = -100123456789
        norm_gid = self.db.normalize_gid(gid)

        # Insert interaction with zero-width space in target_name
        async with self.db.lock:
            self.db.cursor.execute('''
                INSERT INTO interaction_network (group_id, source_user_id, source_name, target_user_id, target_name)
                VALUES (?, ?, ?, ?, ?)
            ''', (norm_gid, 1001, "Normal User", 1002, "\u200B\u200C\uFEFF"))
            self.db.conn.commit()

        graph = await self.db.get_social_graph(group_id=gid)
        nodes = graph.get("nodes", [])
        target_node = next((n for n in nodes if n["id"] == 1002), None)
        self.assertIsNotNone(target_node)
        self.assertNotEqual(target_node["label"], "")
        self.assertNotEqual(target_node["label"], "\u200B\u200C\uFEFF")
        self.assertEqual(target_node["label"], "User #1002")

    async def test_get_group_entity_normalization(self):
        # Test entity resolution handles -100..., 100..., and bare IDs
        from core.engine import SuperBanEngine
        engine = SuperBanEngine(self.mock_engine.client, self.db)
        mock_entity = MagicMock(id=1987654321, title="Normalized Test Group")
        engine._dialogs_cache = [
            {"id": -1001987654321, "title": "Normalized Test Group", "entity": mock_entity}
        ]

        # 1. Lookup by negative channel ID
        ent1 = await engine.get_group_entity(-1001987654321)
        self.assertIsNotNone(ent1)
        self.assertEqual(ent1.title, "Normalized Test Group")

        # 2. Lookup by positive ID with 100 prefix (from abs(gid))
        ent2 = await engine.get_group_entity(1001987654321)
        self.assertIsNotNone(ent2)
        self.assertEqual(ent2.title, "Normalized Test Group")

        # 3. Lookup by bare ID
        ent3 = await engine.get_group_entity(1987654321)
        self.assertIsNotNone(ent3)
        self.assertEqual(ent3.title, "Normalized Test Group")


import json
import time

class AsgiResponse:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status_code = status
        self.headers = headers
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

    async def get(self, path: str, headers: dict = None) -> AsgiResponse:
        return await self.request("GET", path, headers=headers)

    async def post(self, path: str, json: dict = None, headers: dict = None) -> AsgiResponse:
        return await self.request("POST", path, headers=headers, json_data=json)

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

        response_status = None
        response_headers = {}
        response_body = bytearray()
        body_sent = False

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_status, response_headers, response_body
            if message["type"] == "http.response.start":
                response_status = message["status"]
                for k, v in message.get("headers", []):
                    key = k.decode("latin1").lower()
                    val = v.decode("latin1")
                    if key in response_headers:
                        response_headers[key] += f", {val}"
                    else:
                        response_headers[key] = val
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(response_status, response_headers, bytes(response_body))


from web.app import create_app, OWNER_TOKENS


class TestReconApiEndpoints(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="kage_test_api_")
        self.db_path = os.path.join(self.test_dir, "test_api.db")
        self.db = DatabaseManager(self.db_path)
        self.db.init_schema()

        self.mock_client = MagicMock()
        self.mock_client.is_connected = MagicMock(return_value=True)
        self.mock_client.is_user_authorized = AsyncMock(return_value=True)

        from core.engine import SuperBanEngine
        self.engine = SuperBanEngine(self.mock_client, self.db)
        self.engine.get_group_entity = AsyncMock(return_value=MagicMock(id=12345, title="Api Test Group"))
        self.engine.deep_scan_group_messages = AsyncMock(return_value={
            "status": "completed", "scanned": 500, "unique_members": 20
        })

        self.app = create_app(self.engine)
        self.client = InProcessAsgiClient(self.app)

        # Generate authenticated owner token
        self.token = "test_owner_secret_token_123"
        OWNER_TOKENS[self.token] = time.time() + 3600
        self.auth_headers = {"X-Owner-Token": self.token}

    async def asyncTearDown(self):
        self.engine.recon_scheduler.stop()
        self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_unauthenticated_access_blocked(self):
        # Accessing recon schedule without token must return 401
        res = await self.client.get("/api/recon/schedule?group_id=12345")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.headers.get("x-owner-required"), "true")

    async def test_authenticated_get_and_post_schedule(self):
        gid = 12345
        # 1. Get default schedule
        res = await self.client.get(f"/api/recon/schedule?group_id={gid}", headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertFalse(data["schedule"]["is_enabled"])

        # 2. Save schedule
        payload = {
            "group_id": gid,
            "is_enabled": True,
            "schedule_time": "05:30",
            "timezone": "America/New_York",
            "frequency": "daily",
            "target_limit": 50000
        }
        post_res = await self.client.post("/api/recon/schedule", json=payload, headers=self.auth_headers)
        self.assertEqual(post_res.status_code, 200)
        saved = post_res.json()
        self.assertEqual(saved["status"], "success")
        self.assertTrue(saved["schedule"]["is_enabled"])
        self.assertEqual(saved["schedule"]["schedule_time"], "05:30")
        self.assertEqual(saved["schedule"]["timezone"], "America/New_York")
        self.assertEqual(saved["schedule"]["target_limit"], 50000)

        # 3. Retrieve updated schedule
        get_res2 = await self.client.get(f"/api/recon/schedule?group_id={gid}", headers=self.auth_headers)
        self.assertEqual(get_res2.status_code, 200)
        data2 = get_res2.json()
        self.assertTrue(data2["schedule"]["is_enabled"])
        self.assertIsNotNone(data2["next_run_human"])

    async def test_run_now_endpoint_and_history(self):
        gid = 12345
        # 1. Trigger Run Now
        res = await self.client.post("/api/recon/schedule/run_now", json={"group_id": gid}, headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "started")

        # 2. Query History
        hist_res = await self.client.get(f"/api/recon/schedule/history?group_id={gid}&limit=10", headers=self.auth_headers)
        self.assertEqual(hist_res.status_code, 200)
        history = hist_res.json()
        self.assertIsInstance(history, list)
        self.assertTrue(len(history) >= 1)
        self.assertEqual(history[0]["group_id"], self.db.normalize_gid(gid))


if __name__ == '__main__':
    unittest.main()
