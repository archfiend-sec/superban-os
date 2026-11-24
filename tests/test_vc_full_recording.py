"""
tests/test_vc_full_recording.py
Comprehensive Unit and Integration Test Suite for KAGE Voice Chamber (VC) Continuous Recording
and Automated Telegram Saved Messages Upload.
"""

import os
import sys
import time
import json
import asyncio
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.db import DatabaseManager
from core.engine import SuperBanEngine
from tests.test_security_hardening import InProcessAsgiClient
from web.app import create_app, OPERATOR_SESSIONS


class TestVCFullRecordingDB(unittest.IsolatedAsyncioTestCase):
    """Test SQLite persistence layer for VC recordings."""

    async def asyncSetUp(self):
        self.test_db_path = Path("/tmp/test_vc_recordings.db")
        if self.test_db_path.exists():
            self.test_db_path.unlink()
        self.db = DatabaseManager(db_path=self.test_db_path)
        self.db.init_schema()

    async def asyncTearDown(self):
        if hasattr(self, 'db') and self.db.conn:
            self.db.conn.close()
        if self.test_db_path.exists():
            self.test_db_path.unlink()

    async def test_db_recording_crud_lifecycle(self):
        """Verify full DB CRUD lifecycle for VC recordings."""
        rec_id = "rec_test_12345"
        group_id = -100123456789
        
        # 1. Create recording
        created = await self.db.create_vc_recording(
            rec_id=rec_id,
            group_id=group_id,
            group_title="Alpha Intelligence Group",
            call_id="call_999",
            call_title="Operation Blackout Voice Chamber",
            file_path="/tmp/test_rec.mp3",
            destination="me",
            metadata={"source": "MTProto"}
        )
        self.assertEqual(created["id"], rec_id)
        self.assertEqual(created["state"], "RECORDING")
        self.assertEqual(created["group_id"], group_id)

        # 2. Query active recording
        active = await self.db.get_active_vc_recording(group_id)
        self.assertIsNotNone(active)
        self.assertEqual(active["id"], rec_id)
        self.assertEqual(active["state"], "RECORDING")

        # 3. Update state to PAUSED
        paused = await self.db.update_vc_recording_state(rec_id, "PAUSED", duration_sec=45.2)
        self.assertTrue(paused)
        rec_paused = await self.db.get_vc_recording(rec_id)
        self.assertEqual(rec_paused["state"], "PAUSED")
        self.assertEqual(rec_paused["duration_sec"], 45.2)

        # 4. Update state to COMPLETED with message id
        completed = await self.db.update_vc_recording_state(
            rec_id,
            "COMPLETED",
            duration_sec=120.5,
            file_size_bytes=1048576,
            telegram_message_id=98765
        )
        self.assertTrue(completed)
        rec_completed = await self.db.get_vc_recording(rec_id)
        self.assertEqual(rec_completed["state"], "COMPLETED")
        self.assertEqual(rec_completed["telegram_message_id"], 98765)
        self.assertEqual(rec_completed["file_size_bytes"], 1048576)

        # 5. List recordings
        recs = await self.db.list_vc_recordings(group_id=group_id, limit=10)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["id"], rec_id)


class TestVCFullRecordingEngine(unittest.IsolatedAsyncioTestCase):
    """Test SuperBanEngine continuous recording engine methods."""

    async def asyncSetUp(self):
        self.mock_client = MagicMock()
        self.mock_client.is_connected.return_value = True
        self.mock_client.is_user_authorized = AsyncMock(return_value=True)
        self.mock_client.get_me = AsyncMock(return_value=MagicMock(id=12345))
        self.mock_client.send_file = AsyncMock(return_value=MagicMock(id=77777))

        self.mock_db = MagicMock()
        self.mock_db.create_vc_recording = AsyncMock()
        self.mock_db.update_vc_recording_state = AsyncMock(return_value=True)
        self.mock_db.get_vc_recording = AsyncMock(return_value=None)
        self.mock_db.get_active_vc_recording = AsyncMock(return_value=None)
        self.mock_db.list_vc_recordings = AsyncMock(return_value=[])
        self.mock_db.get_vc_talker_leaderboard = AsyncMock(return_value=[
            {"user_id": 111, "full_name": "Spectre Agent", "total_spoke_sec": 42.0}
        ])

        self.engine = SuperBanEngine(client=self.mock_client, db=self.mock_db)

    async def test_recording_start_pause_resume_stop_lifecycle(self):
        """Verify full recording lifecycle state transitions and telemetry."""
        group_id = -100999888
        
        # Start
        start_res = await self.engine.start_vc_recording(group_id, call_title="Live Recon Call")
        self.assertTrue(start_res["success"])
        self.assertEqual(start_res["state"], "RECORDING")
        self.assertTrue(start_res["active"])
        rec_id = start_res["recording_id"]
        self.assertIsNotNone(rec_id)
        self.assertEqual(start_res["destination"], "me")

        # Pause
        pause_res = await self.engine.pause_vc_recording(group_id, recording_id=rec_id)
        self.assertEqual(pause_res["state"], "PAUSED")
        self.assertTrue(pause_res["active"])

        # Resume
        resume_res = await self.engine.resume_vc_recording(group_id, recording_id=rec_id)
        self.assertEqual(resume_res["state"], "RECORDING")

        # Stop with auto_upload=False
        stop_res = await self.engine.stop_vc_recording(group_id, recording_id=rec_id, auto_upload=False)
        self.assertEqual(stop_res["state"], "STOPPED")
        self.assertFalse(stop_res["active"])
        self.assertGreater(stop_res["duration_seconds"], 0)

        # Upload
        upload_res = await self.engine.upload_vc_recording_to_saved(rec_id)
        self.assertEqual(upload_res["state"], "COMPLETED")
        self.assertEqual(upload_res["telegram_message_id"], 77777)
        self.mock_client.send_file.assert_called_once()

    async def test_duplicate_upload_prevention(self):
        """Verify duplicate uploads are strictly prevented if already COMPLETED."""
        group_id = -100555444
        start_res = await self.engine.start_vc_recording(group_id)
        rec_id = start_res["recording_id"]

        # Stop and upload
        await self.engine.stop_vc_recording(group_id, recording_id=rec_id, auto_upload=True)
        self.assertEqual(self.mock_client.send_file.call_count, 1)

        # Attempt second upload on same recording
        dup_res = await self.engine.upload_vc_recording_to_saved(rec_id)
        self.assertEqual(dup_res["state"], "COMPLETED")
        self.assertEqual(dup_res["telegram_message_id"], 77777)
        # Call count must still be 1 (no duplicate sent to Telegram)
        self.assertEqual(self.mock_client.send_file.call_count, 1)

    async def test_retry_upload_on_failure(self):
        """Verify retry_upload handles initial failures and successfully recovers."""
        group_id = -100333222
        start_res = await self.engine.start_vc_recording(group_id)
        rec_id = start_res["recording_id"]

        # Make client fail on first attempt
        self.mock_client.send_file.side_effect = ConnectionResetError("Telegram MTProto Network Timeout")

        stop_res = await self.engine.stop_vc_recording(group_id, recording_id=rec_id, auto_upload=True)
        self.assertEqual(stop_res["state"], "FAILED")
        self.assertIn("Timeout", stop_res["error_message"])

        # Recover client and retry
        self.mock_client.send_file.side_effect = None
        self.mock_client.send_file.return_value = MagicMock(id=88888)

        retry_res = await self.engine.retry_vc_recording_upload(rec_id)
        self.assertEqual(retry_res["state"], "COMPLETED")
        self.assertEqual(retry_res["telegram_message_id"], 88888)
        self.assertIsNone(retry_res["error_message"])

    async def test_append_vc_recording_chunk(self):
        """Verify client audio chunks can be received and appended to session."""
        group_id = -100777666
        start_res = await self.engine.start_vc_recording(group_id)
        rec_id = start_res["recording_id"]

        chunk_data = b"RIFF" + b"\x00" * 200
        res = await self.engine.append_vc_recording_chunk(group_id, chunk_data, recording_id=rec_id)
        self.assertTrue(res["success"])
        self.assertEqual(res["recording_id"], rec_id)


class TestVCFullRecordingAPI(unittest.IsolatedAsyncioTestCase):
    """Integration test suite for VC recording REST endpoints."""

    async def asyncSetUp(self):
        self.mock_client = MagicMock()
        self.mock_client.is_connected.return_value = True
        self.mock_client.is_user_authorized = AsyncMock(return_value=True)
        self.mock_client.get_me = AsyncMock(return_value=MagicMock(id=12345))
        self.mock_client.send_file = AsyncMock(return_value=MagicMock(id=54321))

        self.mock_db = MagicMock()
        self.mock_db.create_vc_recording = AsyncMock()
        self.mock_db.update_vc_recording_state = AsyncMock(return_value=True)
        self.mock_db.get_vc_recording = AsyncMock(return_value=None)
        self.mock_db.get_active_vc_recording = AsyncMock(return_value=None)
        self.mock_db.list_vc_recordings = AsyncMock(return_value=[])
        self.mock_db.get_vc_talker_leaderboard = AsyncMock(return_value=[])

        self.engine = SuperBanEngine(client=self.mock_client, db=self.mock_db)

        self.session_token = "vc_rec_test_sec_token_999"
        OPERATOR_SESSIONS[self.session_token] = {
            "username": "owner",
            "role": "owner",
            "expires": time.time() + 3600
        }

        self.auth_headers = {
            "x-owner-token": self.session_token,
            "Authorization": f"Bearer {self.session_token}",
            "Cookie": f"owner_token={self.session_token}; kage_session={self.session_token}"
        }

        self.app = create_app(self.engine)
        self.client = InProcessAsgiClient(self.app)

    async def asyncTearDown(self):
        if self.session_token in OPERATOR_SESSIONS:
            del OPERATOR_SESSIONS[self.session_token]

    async def test_recording_endpoints_full_cycle(self):
        """Verify start, status, pause, resume, stop, retry and download endpoints."""
        gid = -100444333

        # 1. Start recording
        res = await self.client.request(
            "POST",
            "/api/vc/recording/start",
            json_data={"group_id": gid, "call_title": "Tactical Recon"},
            headers=self.auth_headers
        )
        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data["state"], "RECORDING")
        rec_id = data["recording_id"]

        # 2. Get status
        res_stat = await self.client.request(
            "GET",
            f"/api/vc/recording/status?group_id={gid}",
            headers=self.auth_headers
        )
        self.assertEqual(res_stat.status, 200)
        stat_data = res_stat.json()
        self.assertEqual(stat_data["recording_id"], rec_id)
        self.assertEqual(stat_data["destination"], "me")

        # 3. Pause
        res_pause = await self.client.request(
            "POST",
            "/api/vc/recording/pause",
            json_data={"group_id": gid, "recording_id": rec_id},
            headers=self.auth_headers
        )
        self.assertEqual(res_pause.status, 200)
        self.assertEqual(res_pause.json()["state"], "PAUSED")

        # 4. Resume
        res_resume = await self.client.request(
            "POST",
            "/api/vc/recording/resume",
            json_data={"group_id": gid, "recording_id": rec_id},
            headers=self.auth_headers
        )
        self.assertEqual(res_resume.status, 200)
        self.assertEqual(res_resume.json()["state"], "RECORDING")

        # 5. Stop and auto-upload
        res_stop = await self.client.request(
            "POST",
            "/api/vc/recording/stop",
            json_data={"group_id": gid, "recording_id": rec_id, "auto_upload": True},
            headers=self.auth_headers
        )
        self.assertEqual(res_stop.status, 200)
        stop_data = res_stop.json()
        self.assertEqual(stop_data["state"], "COMPLETED")
        self.assertEqual(stop_data["telegram_message_id"], 54321)

        # 6. Retry upload endpoint
        res_retry = await self.client.request(
            "POST",
            "/api/vc/recording/retry_upload",
            json_data={"recording_id": rec_id},
            headers=self.auth_headers
        )
        self.assertEqual(res_retry.status, 200)
        self.assertEqual(res_retry.json()["telegram_message_id"], 54321)

        # 7. Download recording
        res_dl = await self.client.request(
            "GET",
            f"/api/vc/recording/download?recording_id={rec_id}",
            headers=self.auth_headers
        )
        self.assertEqual(res_dl.status, 200)
        self.assertIn("audio/mpeg", res_dl.headers.get("content-type", ""))

    async def test_unauthenticated_recording_access_strictly_denied(self):
        """Verify unauthenticated requests to recording endpoints are rejected with 401."""
        res = await self.client.request(
            "POST",
            "/api/vc/recording/start",
            json_data={"group_id": 12345}
        )
        self.assertEqual(res.status, 401)


if __name__ == "__main__":
    unittest.main()
