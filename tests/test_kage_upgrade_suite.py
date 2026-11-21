import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import DatabaseManager
from core.sentinel import SentinelShield
from core.engine import SuperBanEngine
from web.app import create_app, OPERATOR_SESSIONS, OWNER_TOKENS

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

        if "?" in path:
            path, qs = path.split("?", 1)
            query_string = qs

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii") if isinstance(query_string, str) else query_string,
            "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in req_headers.items()],
            "client": (client_ip, 54321),
            "server": ("127.0.0.1", 8000),
        }

        response_started = False
        status_code = 500
        resp_headers = {}
        resp_body = bytearray()

        body_sent = False
        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started, status_code, resp_headers, resp_body
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                for k, v in message.get("headers", []):
                    resp_headers[k.decode("latin-1").lower()] = v.decode("latin-1")
            elif message["type"] == "http.response.body":
                resp_body.extend(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(status_code, resp_headers, bytes(resp_body))

    async def get(self, path: str, headers: dict = None, query_string: str = "") -> AsgiResponse:
        return await self.request("GET", path, headers=headers, query_string=query_string)

    async def post(self, path: str, json_data: dict = None, headers: dict = None, query_string: str = "") -> AsgiResponse:
        return await self.request("POST", path, headers=headers, json_data=json_data, query_string=query_string)

    async def delete(self, path: str, headers: dict = None, query_string: str = "") -> AsgiResponse:
        return await self.request("DELETE", path, headers=headers, query_string=query_string)


class TestKageUpgradeSuite(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="kage_test_upgrade_")
        self.db_path = os.path.join(self.test_dir, "test_upgrade.db")
        self.db = DatabaseManager(self.db_path)
        self.db.init_schema()

        # Mock SuperBanEngine
        self.mock_engine = MagicMock(spec=SuperBanEngine)
        self.mock_engine.db = self.db
        self.mock_engine.normalize_gid = self.db.normalize_gid
        self.mock_engine.active_deep_scans = {}
        self.mock_engine.active_siphon_tasks = {}
        self.mock_engine.active_dispatch_tasks = {}
        self.mock_engine._cancel_siphon_tasks = set()
        self.mock_engine._cancel_dispatch_tasks = set()
        self.mock_engine.client = MagicMock()
        self.mock_engine.client.is_connected = MagicMock(return_value=True)
        self.mock_engine.client.is_user_authorized = AsyncMock(return_value=True)
        self.mock_engine.emit_telemetry = AsyncMock()
        self.mock_engine.sentinel = SentinelShield(self.mock_engine.client, self.db)
        self.mock_engine.get_group_entity = AsyncMock(return_value=MagicMock(id=123456789, title="War Room Primary"))
        self.mock_engine.clean_service_messages = AsyncMock(return_value=15)

        # Engine methods delegated to real DB & logic
        self.mock_engine.start_siphon_collection = SuperBanEngine.start_siphon_collection.__get__(self.mock_engine, SuperBanEngine)
        self.mock_engine.stop_siphon_collection = SuperBanEngine.stop_siphon_collection.__get__(self.mock_engine, SuperBanEngine)
        self.mock_engine.resume_siphon_collection = SuperBanEngine.resume_siphon_collection.__get__(self.mock_engine, SuperBanEngine)
        self.mock_engine.dispatch_multi_group_messages = SuperBanEngine.dispatch_multi_group_messages.__get__(self.mock_engine, SuperBanEngine)
        self.mock_engine.stop_dispatch_job = SuperBanEngine.stop_dispatch_job.__get__(self.mock_engine, SuperBanEngine)
        self.mock_engine.retry_dispatch_failed = SuperBanEngine.retry_dispatch_failed.__get__(self.mock_engine, SuperBanEngine)
        self.mock_engine.preview_service_clutter = SuperBanEngine.preview_service_clutter.__get__(self.mock_engine, SuperBanEngine)
        self.mock_engine.get_authorized_sources = AsyncMock(return_value=[
            {"id": -100123456789, "title": "War Room Primary", "type": "supergroup", "participants_count": 18500},
            {"id": -100987654321, "title": "Field Intel Outpost", "type": "channel", "participants_count": 4200}
        ])

        # Create FastAPI app
        self.app = create_app(self.mock_engine)
        self.client = InProcessAsgiClient(self.app)

        # Create active authenticated operator session with owner clearance
        self.token = "test_clearance_token_9999"
        now = time.time()
        OPERATOR_SESSIONS[self.token] = {
            "username": "commander",
            "role": "owner",
            "expires": now + 7200
        }
        OWNER_TOKENS[self.token] = now + 7200
        self.auth_headers = {
            "X-Owner-Token": self.token,
            "Authorization": f"Bearer {self.token}"
        }

    async def asyncTearDown(self):
        OPERATOR_SESSIONS.pop(self.token, None)
        OWNER_TOKENS.pop(self.token, None)
        try:
            self.db.close()
        except Exception:
            pass
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------
    # 1. CRYPTO REMOVAL & IOC PRESERVATION
    # -------------------------------------------------------------
    async def test_crypto_removal_and_ioc_preservation(self):
        sentinel = SentinelShield(MagicMock(), self.db)
        
        # Crypto wallet/contract addresses should NOT be extracted as entities
        eth_text = "Check this address 0x1234567890abcdef1234567890abcdef12345678"
        sol_text = "Solana token 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
        
        entities_eth = sentinel._extract_entities(eth_text, -1001, "Test", 1, 2, "Anon")
        entities_sol = sentinel._extract_entities(sol_text, -1001, "Test", 2, 2, "Anon")
        
        types_eth = [e["entity_type"] for e in entities_eth]
        types_sol = [e["entity_type"] for e in entities_sol]
        
        self.assertNotIn("crypto_eth", types_eth)
        self.assertNotIn("crypto_sol", types_sol)
        self.assertEqual(len(entities_eth), 0)
        self.assertEqual(len(entities_sol), 0)

        # General threat indicators (IOCs, phishing links, channel invites, cashtags) MUST be preserved
        url_text = "Join secret leak at https://t.me/+join_test_invite and check $TARGET"
        entities_ioc = sentinel._extract_entities(url_text, -1001, "Test", 3, 2, "Anon")
        self.assertTrue(len(entities_ioc) > 0)
        ioc_types = [e["entity_type"] for e in entities_ioc]
        self.assertIn("tg_invite", ioc_types)
        self.assertIn("cashtag", ioc_types)

        # Verify /api/crypto/dexscreener endpoint is removed
        res = await self.client.get("/api/crypto/dexscreener", query_string="pair=0x1234", headers=self.auth_headers)
        self.assertEqual(res.status, 404, "Deprecated crypto DexScreener endpoint must return 404")

    # -------------------------------------------------------------
    # 2. AUTONOMOUS SIPHON MATRIX
    # -------------------------------------------------------------
    async def test_autonomous_siphon_matrix_lifecycle(self):
        # 1. Get Authorized Sources
        res_sources = await self.client.get("/api/siphon/sources", headers=self.auth_headers)
        self.assertEqual(res_sources.status, 200)
        sources_data = res_sources.json()
        self.assertEqual(len(sources_data), 2)

        # 2. Start Siphon Ingestion Job
        res_job = await self.client.post("/api/siphon/jobs", json_data={
            "group_id": -100123456789,
            "limit": 500,
            "filter_media": "all",
            "keyword": "intel"
        }, headers=self.auth_headers)
        self.assertEqual(res_job.status, 200)
        job_data = res_job.json()
        job_id = job_data["job_id"]
        self.assertTrue(job_id.startswith("siph_"))

        # 3. Duplicate Prevention: Attempt to launch another active job on same group
        res_dup = await self.client.post("/api/siphon/jobs", json_data={
            "group_id": -100123456789,
            "limit": 100
        }, headers=self.auth_headers)
        self.assertEqual(res_dup.status, 400, "Active siphon job for group must reject duplicate")

        # 4. List Jobs
        res_jobs = await self.client.get("/api/siphon/jobs", headers=self.auth_headers)
        self.assertEqual(res_jobs.status, 200)
        jobs_list = res_jobs.json()
        self.assertTrue(any(j["id"] == job_id for j in jobs_list))

        # 5. Insert simulated collected record and query
        await self.db.save_siphon_collected_record(
            job_id=job_id,
            group_id=-100123456789,
            group_title="War Room Primary",
            message_id=98765,
            sender_id=112233,
            sender_name="Shadow Agent",
            sender_username="shadow_op",
            message_text="Operation Blackout confirmed.",
            message_type="text"
        )

        res_records = await self.client.get("/api/siphon/records", query_string=f"job_id={job_id}", headers=self.auth_headers)
        self.assertEqual(res_records.status, 200)
        records = res_records.json()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["message_text"], "Operation Blackout confirmed.")

        # 6. Stop and Resume Siphon Job
        res_stop = await self.client.post(f"/api/siphon/jobs/{job_id}/stop", headers=self.auth_headers)
        self.assertEqual(res_stop.status, 200)
        self.assertTrue(res_stop.json()["success"])

        res_resume = await self.client.post(f"/api/siphon/jobs/{job_id}/resume", headers=self.auth_headers)
        self.assertEqual(res_resume.status, 200)

        # 7. CSV Export
        res_export = await self.client.get("/api/siphon/export", query_string=f"job_id={job_id}", headers=self.auth_headers)
        self.assertEqual(res_export.status, 200)
        self.assertIn("text/csv", res_export.headers.get("content-type", ""))
        self.assertIn("Operation Blackout confirmed", res_export.text)

    # -------------------------------------------------------------
    # 3. MULTI-GROUP DISPATCHER
    # -------------------------------------------------------------
    async def test_multi_group_dispatcher_lifecycle(self):
        # 1. Create dispatch job
        res_dispatch = await self.client.post("/api/dispatcher/jobs", json_data={
            "title": "Obsidian Emergency Broadcast",
            "group_ids": [-100123456789, -100987654321],
            "message_text": "ATTENTION: Obsidian protocol active across nodes.",
            "batch_size": 1,
            "sending_interval_sec": 0.5
        }, headers=self.auth_headers)
        self.assertEqual(res_dispatch.status, 200)
        dispatch_data = res_dispatch.json()
        job_id = dispatch_data["job_id"]
        self.assertTrue(job_id.startswith("disp_"))

        # 2. Query dispatch jobs
        res_jobs = await self.client.get("/api/dispatcher/jobs", headers=self.auth_headers)
        self.assertEqual(res_jobs.status, 200)
        jobs = res_jobs.json()
        self.assertTrue(any(j["id"] == job_id for j in jobs))

        # 3. Stop dispatch job
        res_stop = await self.client.post(f"/api/dispatcher/jobs/{job_id}/stop", headers=self.auth_headers)
        self.assertEqual(res_stop.status, 200)

        # 4. Retry failed deliveries
        # Mark one delivery failed
        self.db.cursor.execute("UPDATE multi_group_dispatch_deliveries SET status='failed', error_message='FloodWait' WHERE job_id=?", (job_id,))
        self.db.conn.commit()

        res_retry = await self.client.post(f"/api/dispatcher/jobs/{job_id}/retry", headers=self.auth_headers)
        self.assertEqual(res_retry.status, 200)
        self.assertIn(res_retry.json()["status"], ("running", "started"))
        self.assertTrue(res_retry.json()["job_id"].startswith("retry_"))

    # -------------------------------------------------------------
    # 4. CLUTTER SWEEPER & SYSTEM MAINTENANCE
    # -------------------------------------------------------------
    async def test_clutter_sweeper_and_maintenance(self):
        # Insert duplicate security audit logs
        self.db.cursor.execute("INSERT INTO security_audit_logs (event_type, username, detail, timestamp) VALUES ('login', 'admin', 'duplicate detail', '2026-11-21 10:00:00')")
        self.db.cursor.execute("INSERT INTO security_audit_logs (event_type, username, detail, timestamp) VALUES ('login', 'admin', 'duplicate detail', '2026-11-21 10:00:00')")
        self.db.conn.commit()

        # 1. Inspect Clutter
        res_inspect = await self.client.get("/api/maintenance/inspect", headers=self.auth_headers)
        self.assertEqual(res_inspect.status, 200)
        inspect_data = res_inspect.json()
        self.assertIn("db_size_bytes", inspect_data)
        self.assertIn("total_clutter_records", inspect_data)
        self.assertGreaterEqual(inspect_data["duplicate_security_logs"], 1)

        # 2. Preview Service Clutter
        res_svc = await self.client.get("/api/maintenance/service_clutter", query_string="group_id=-100123456789", headers=self.auth_headers)
        self.assertEqual(res_svc.status, 200)
        self.assertIn("service_messages_found", res_svc.json())

        # 3. Clean Clutter (remove duplicate security logs)
        res_clean = await self.client.post("/api/maintenance/clean", json_data={
            "options": {"duplicate_security_logs": True}
        }, headers=self.auth_headers)
        self.assertEqual(res_clean.status, 200)
        clean_result = res_clean.json()["purged"]
        self.assertIn("duplicate_security_logs", clean_result)
        self.assertEqual(clean_result["duplicate_security_logs"], 1)

    # -------------------------------------------------------------
    # 5. THREAT INTELLIGENCE RADAR
    # -------------------------------------------------------------
    async def test_threat_intelligence_radar(self):
        # 1. Record Threat Indicators
        await self.db.record_threat_indicator(
            indicator_type="suspicious_url",
            indicator_value="http://dark-credential-phish.ru",
            group_id=-100123456789,
            group_title="War Room Primary",
            message_id=5555,
            sender_id=7777,
            sender_name="Shadow Operator",
            severity="CRITICAL",
            context="Known credential harvesting endpoint"
        )
        await self.db.record_threat_indicator(
            indicator_type="cashtag",
            indicator_value="$SUSP",
            group_id=-100123456789,
            group_title="War Room Primary",
            message_id=5556,
            sender_id=7777,
            sender_name="Shadow Operator",
            severity="MEDIUM",
            context="Aggressive pump cashtag"
        )

        # 2. Query Indicators with filter
        res_radar = await self.client.get("/api/radar/indicators", query_string="severity=CRITICAL", headers=self.auth_headers)
        self.assertEqual(res_radar.status, 200)
        indicators = res_radar.json()
        self.assertEqual(len(indicators), 1)
        self.assertEqual(indicators[0]["indicator_type"], "suspicious_url")
        ind_id = indicators[0]["id"]

        # 3. Radar Stats
        res_stats = await self.client.get("/api/radar/stats", headers=self.auth_headers)
        self.assertEqual(res_stats.status, 200)
        stats = res_stats.json()
        self.assertGreaterEqual(stats["total"], 2)
        self.assertGreaterEqual(stats["critical"], 1)

        # 4. Acknowledge Indicator
        res_ack = await self.client.post(f"/api/radar/indicators/{ind_id}/acknowledge", headers=self.auth_headers)
        self.assertEqual(res_ack.status, 200)
        self.assertTrue(res_ack.json()["success"])

        # 5. Export Radar CSV
        res_export = await self.client.get("/api/radar/export", headers=self.auth_headers)
        self.assertEqual(res_export.status, 200)
        self.assertIn("text/csv", res_export.headers.get("content-type", ""))
        self.assertIn("dark-credential-phish.ru", res_export.text)

    # -------------------------------------------------------------
    # 6. COMPACT SYSTEM HEALTH & LOGOUT SECURITY
    # -------------------------------------------------------------
    async def test_system_health_and_explicit_logout(self):
        # 1. System Health
        res_health = await self.client.get("/api/system/health", headers=self.auth_headers)
        self.assertEqual(res_health.status, 200)
        hdata = res_health.json()
        self.assertTrue(hdata["telegram_connected"])
        self.assertTrue(hdata["backend_healthy"])
        self.assertIn("active_jobs_count", hdata)
        self.assertIn("last_sync", hdata)

        # 2. Explicit Logout Revocation
        res_logout = await self.client.post("/api/owner/logout", headers=self.auth_headers)
        self.assertEqual(res_logout.status, 200)
        self.assertNotIn(self.token, OPERATOR_SESSIONS, "Session token must be revoked immediately on logout")
        self.assertNotIn(self.token, OWNER_TOKENS, "Owner token must be revoked immediately on logout")

        # Subsequent requests must be 401 Unauthorized
        res_denied = await self.client.get("/api/radar/stats", headers=self.auth_headers)
        self.assertEqual(res_denied.status, 401, "Revoked session must be denied access")


if __name__ == "__main__":
    unittest.main()
