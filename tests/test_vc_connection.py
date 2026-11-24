"""
tests/test_vc_connection.py
Comprehensive Unit and Integration Test Suite for KAGE Voice Chamber (VC) Connection Engine.
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

from telethon.tl.types import InputGroupCall, GroupCall, GroupCallDiscarded, InputPeerSelf
from telethon.errors import ChannelPrivateError, ChatAdminRequiredError
from core.engine import SuperBanEngine, VCConnectionState
from tests.test_security_hardening import InProcessAsgiClient
from web.app import create_app


class TestVCConnectionLifecycle(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.mock_client = MagicMock()
        self.mock_client.is_connected.return_value = True
        self.mock_client.is_user_authorized = AsyncMock(return_value=True)
        self.mock_client.get_me = AsyncMock(return_value=MagicMock(id=12345))

        self.mock_db = MagicMock()
        self.mock_db.record_vc_speaker_activity = AsyncMock()
        self.mock_db.get_vc_talker_leaderboard = AsyncMock(return_value=[])

        self.engine = SuperBanEngine(client=self.mock_client, db=self.mock_db)

    async def test_vc_states_definition(self):
        """Verify all 13 required connection states are formally defined."""
        required_states = [
            "IDLE", "VALIDATING", "RESOLVING_TARGET", "CHECKING_GROUP_CALL",
            "JOINING", "INITIALIZING_AUDIO", "CONNECTED", "RECONNECTING",
            "DISCONNECTING", "DISCONNECTED", "FAILED", "PERMISSION_DENIED", "NO_ACTIVE_CALL"
        ]
        for state in required_states:
            self.assertTrue(hasattr(VCConnectionState, state), f"Missing state {state}")
            self.assertEqual(getattr(VCConnectionState, state), state)

    async def test_get_group_call_input_caching_and_force(self):
        """Verify _get_group_call_input respects caching and bypasses on force=True."""
        mock_entity = MagicMock(id=12345)
        mock_call = MagicMock(id=999, access_hash=888)
        mock_full = MagicMock()
        mock_full.full_chat.call = mock_call

        call_fn = AsyncMock(return_value=mock_full)
        self.mock_client.side_effect = call_fn
        
        # First call: populates cache
        res1 = await self.engine._get_group_call_input(mock_entity, force=False)
        self.assertIsNotNone(res1)
        self.assertEqual(res1.id, 999)
        self.assertEqual(call_fn.call_count, 1)

        # Second call within TTL: served from cache without invoking client
        res2 = await self.engine._get_group_call_input(mock_entity, force=False)
        self.assertEqual(res2.id, 999)
        self.assertEqual(call_fn.call_count, 1)

        # Third call with force=True: must bypass cache and re-query client
        res3 = await self.engine._get_group_call_input(mock_entity, force=True)
        self.assertEqual(res3.id, 999)
        self.assertEqual(call_fn.call_count, 2)

    async def test_permission_denied_handling(self):
        """Verify access restriction errors set PERMISSION_DENIED state."""
        mock_entity = MagicMock(id=55555)
        call_fn = AsyncMock(side_effect=ChannelPrivateError(request=None))
        self.mock_client.side_effect = call_fn

        with self.assertRaises(PermissionError):
            await self.engine.join_voice_call(mock_entity)

        self.assertEqual(self.engine.vc_connection_state, VCConnectionState.PERMISSION_DENIED)
        self.assertIn("Access Denied", self.engine.vc_connection_stage)

    async def test_no_active_call_handling(self):
        """Verify missing voice chat sets NO_ACTIVE_CALL state."""
        mock_entity = MagicMock(id=66666)
        mock_full = MagicMock()
        mock_full.full_chat.call = None
        call_fn = AsyncMock(return_value=mock_full)
        self.mock_client.side_effect = call_fn

        with self.assertRaises(ValueError):
            await self.engine.join_voice_call(mock_entity)

        self.assertEqual(self.engine.vc_connection_state, VCConnectionState.NO_ACTIVE_CALL)
        self.assertIn("No Active", self.engine.vc_connection_stage)

    async def test_successful_join_and_leave_lifecycle(self):
        """Verify full progression from VALIDATING to CONNECTED and DISCONNECTED."""
        mock_entity = MagicMock(id=77777, broadcast=False, megagroup=True)
        mock_call = MagicMock(id=111, access_hash=222)
        mock_full = MagicMock()
        mock_full.full_chat.call = mock_call

        join_as_res = MagicMock(peers=[])
        join_res = MagicMock(updates=[])
        check_res = MagicMock()
        leave_res = MagicMock()
        call_fn = AsyncMock(side_effect=[mock_full, join_as_res, join_res, check_res, leave_res])
        self.mock_client.side_effect = call_fn

        # Prevent actual PyTgCalls initialization in unit tests
        with patch.object(self.engine, '_ensure_pytgcalls', return_value=None):
            result = await self.engine.join_voice_call(mock_entity, muted=True)

        self.assertTrue(result.get("success"))
        self.assertEqual(self.engine.vc_connection_state, VCConnectionState.CONNECTED)
        self.assertTrue(self.engine.is_in_voice_call(77777))
        self.assertIsNotNone(self.engine.vc_connection_id)

        # Test leave
        leave_result = await self.engine.leave_voice_call(mock_entity)
        self.assertTrue(leave_result.get("success"))
        self.assertEqual(self.engine.vc_connection_state, VCConnectionState.DISCONNECTED)
        self.assertFalse(self.engine.is_in_voice_call(77777))
        self.assertIsNone(self.engine.active_vc_session)

    async def test_connection_summary_telemetry(self):
        """Verify get_vc_connection_summary returns all required fields."""
        summary = self.engine.get_vc_connection_summary()
        required_keys = [
            "connection_state", "connection_stage", "connection_id",
            "connection_error", "active_group_id", "is_connected",
            "connected_at", "audio_state", "audio_stream_available"
        ]
        for key in required_keys:
            self.assertIn(key, summary, f"Missing key {key} in connection summary")


class TestVCAPIRoutes(unittest.IsolatedAsyncioTestCase):

    async def test_api_routes_status_and_connection(self):
        """Verify FastAPI routes /api/vc/status, /api/vc/connection, /api/vc/join, /api/vc/leave."""
        mock_engine = MagicMock(spec=SuperBanEngine)
        mock_engine.db = None
        mock_engine.is_in_voice_call = MagicMock(return_value=False)
        mock_engine.active_vc_session = None
        mock_engine.state = "RUNNING"
        mock_engine.active_group = None
        mock_engine.sentinel = MagicMock()
        mock_engine.sentinel.register_callback = MagicMock()
        mock_engine.get_vc_connection_summary = MagicMock(return_value={
            "connection_state": "IDLE",
            "connection_stage": "Idle",
            "connection_id": None,
            "connection_error": None,
            "active_group_id": None,
            "is_connected": False,
            "connected_at": None,
            "audio_state": "IDLE",
            "audio_stream_available": False
        })
        mock_engine.get_group_entity = AsyncMock(return_value=MagicMock(id=12345))
        mock_engine.get_group_call_info = AsyncMock(return_value={
            "has_call": True,
            "status": "LIVE",
            "is_connected": False,
            "connection_state": "IDLE",
            "title": "Test Voice Chat",
            "participants": []
        })
        mock_engine.join_voice_call = AsyncMock(return_value={
            "success": True,
            "connection_state": "CONNECTED"
        })
        mock_engine.leave_voice_call = AsyncMock(return_value={
            "success": True,
            "status": "disconnected"
        })
        mock_engine.register_listener = MagicMock()
        mock_engine.add_event_listener = MagicMock()

        app = create_app(mock_engine)
        client = InProcessAsgiClient(app)

        from web.app import OPERATOR_SESSIONS
        session_token = "kage_test_vc_session_token"
        OPERATOR_SESSIONS[session_token] = {
            "username": "owner",
            "role": "owner",
            "expires": time.time() + 3600
        }
        auth_headers = {
            "Cookie": f"kage_session={session_token}",
            "x-owner-token": session_token
        }

        # 1. Test /api/vc/connection
        resp = await client.request("GET", "/api/vc/connection", headers=auth_headers)
        self.assertEqual(resp.status, 200)
        data = resp.json()
        self.assertEqual(data["connection_state"], "IDLE")

        # 2. Test /api/vc/status
        resp = await client.request("GET", "/api/vc/status?group_id=12345", headers=auth_headers)
        self.assertEqual(resp.status, 200)
        data = resp.json()
        self.assertTrue(data["has_call"])
        self.assertEqual(data["title"], "Test Voice Chat")

        # 3. Test /api/vc/join
        resp = await client.request("POST", "/api/vc/join", json_data={"group_id": 12345, "muted": True}, headers=auth_headers)
        self.assertEqual(resp.status, 200)
        data = resp.json()
        self.assertTrue(data["success"])

        # 4. Test /api/vc/leave
        resp = await client.request("POST", "/api/vc/leave", json_data={"group_id": 12345}, headers=auth_headers)
        self.assertEqual(resp.status, 200)
        data = resp.json()
        self.assertTrue(data["success"])


if __name__ == "__main__":
    unittest.main()
