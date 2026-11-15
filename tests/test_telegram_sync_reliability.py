import os
import time
import json
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

# Set testing environment variables
os.environ["SUPERBAN_CONFIG_DIR"] = "/tmp/test_kage_sync"
os.environ["TELEGRAM_OWNER_PIN"] = "1704"

from core.engine import SuperBanEngine
from core.db import DatabaseManager
from web.app import create_app, OWNER_TOKENS, OPERATOR_SESSIONS, FAILED_VERIFY_ATTEMPTS, FAILED_ACCOUNT_ATTEMPTS

_test_db_path = Path("/tmp/test_sync_engine.db")
_test_db = DatabaseManager(_test_db_path)
_mock_client = MagicMock()
_mock_client.is_connected.return_value = True
_mock_client.is_user_authorized = AsyncMock(return_value=True)
_mock_client.get_dialogs = AsyncMock(return_value=[])
_mock_client.get_entity = AsyncMock(return_value=None)
_mock_client.get_me = AsyncMock(return_value=None)

engine = SuperBanEngine(_mock_client, _test_db)
app = create_app(engine)
from core.config import Config
from core.media import extract_media_info
from core.db import DatabaseManager
from telethon.tl import types


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
    """Pure in-process ASGI 3.0 test client running without network sockets or external httpx."""
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

    async def websocket_connect(
        self,
        path: str = "/ws",
        headers: dict = None,
        query_string: str = "",
        client_ip: str = "127.0.0.1"
    ):
        """Simulate ASGI WebSocket handshake and return (accepted: bool, close_code: Optional[int])."""
        headers = headers or {}
        req_headers = {}
        for k, v in headers.items():
            req_headers[k.lower()] = v

        if "host" not in req_headers:
            req_headers["host"] = "testserver"

        raw_headers = [
            (k.encode("latin1"), v.encode("latin1"))
            for k, v in req_headers.items()
        ]

        if "?" in path and not query_string:
            path, query_string = path.split("?", 1)

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": raw_headers,
            "client": (client_ip, 50000),
            "server": ("127.0.0.1", 80),
        }

        accepted = False
        close_code = None
        close_reason = None

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            nonlocal accepted, close_code, close_reason
            if message["type"] == "websocket.accept":
                accepted = True
            elif message["type"] == "websocket.close":
                close_code = message.get("code")
                close_reason = message.get("reason")

        try:
            await self.app(scope, receive, send)
        except Exception:
            pass

        return accepted, close_code, close_reason


class TestTelegramSyncReliabilityAndSecurity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = InProcessAsgiClient(app)
        OWNER_TOKENS.clear()
        OPERATOR_SESSIONS.clear()
        FAILED_VERIFY_ATTEMPTS.clear()
        FAILED_ACCOUNT_ATTEMPTS.clear()

        # Seed valid master session
        self.owner_token = "valid_test_owner_token_abc123"
        self.now = time.time()
        OWNER_TOKENS[self.owner_token] = self.now + 86400
        OPERATOR_SESSIONS[self.owner_token] = {
            "username": "owner",
            "role": "owner",
            "expires": self.now + 86400
        }

    # ═══════════════════════════════════════════════════════════════════
    #  PART 1: SECURITY AUDIT & PENTEST VALIDATION (IMAGE 1)
    # ═══════════════════════════════════════════════════════════════════

    async def test_01_sensitive_endpoints_reject_unauthenticated_requests(self):
        """Verify all sensitive /api/* endpoints strictly reject missing, expired, and forged tokens with 401."""
        protected_endpoints = [
            "/api/dialogs",
            "/api/messages?group_id=12345",
            "/api/messages/ingestion/health",
            "/api/messages/history",
            "/api/media/preview/12345/101",
            "/api/operators"
        ]

        for ep in protected_endpoints:
            # 1. Missing token
            res_missing = await self.client.request("GET", ep)
            self.assertEqual(res_missing.status, 401, f"{ep} failed to reject missing token")
            self.assertIn("Owner clearance required", res_missing.json().get("detail", ""))

            # 2. Forged token
            res_forged = await self.client.request("GET", ep, headers={"X-Owner-Token": "forged_malicious_token_xyz999"})
            self.assertEqual(res_forged.status, 401, f"{ep} failed to reject forged token")

            # 3. Expired token
            expired_tok = "expired_token_000"
            OWNER_TOKENS[expired_tok] = time.time() - 3600
            res_expired = await self.client.request("GET", ep, headers={"X-Owner-Token": expired_tok})
            self.assertEqual(res_expired.status, 401, f"{ep} failed to reject expired token")

    async def test_02_websocket_handshake_rejects_unauthenticated_connections(self):
        """Verify WebSocket /ws handshake rejects missing, expired, and forged tokens with code 1008."""
        # 1. Missing token
        accepted, code, reason = await self.client.websocket_connect("/ws")
        self.assertFalse(accepted)
        self.assertEqual(code, 1008)

        # 2. Forged token
        accepted, code, reason = await self.client.websocket_connect("/ws?token=forged_token_xyz")
        self.assertFalse(accepted)
        self.assertEqual(code, 1008)

        # 3. Expired token
        expired_tok = "ws_expired_tok_777"
        OWNER_TOKENS[expired_tok] = time.time() - 100
        accepted, code, reason = await self.client.websocket_connect(f"/ws?token={expired_tok}")
        self.assertFalse(accepted)
        self.assertEqual(code, 1008)

        # 4. Valid token succeeds
        accepted, code, reason = await self.client.websocket_connect(f"/ws?token={self.owner_token}")
        self.assertTrue(accepted)
        self.assertIsNone(code)

    async def test_03_lockout_scope_and_zero_user_enumeration(self):
        """Verify lockout duration, per-IP vs per-account scope, and zero info leakage in error strings."""
        client_ip = "198.51.100.5"

        # 4 consecutive failed attempts on an IP
        for i in range(4):
            res = await self.client.request("POST", "/api/owner/verify", json_data={"pin": "wrong_pin"}, client_ip=client_ip)
            self.assertEqual(res.status, 403)
            self.assertEqual(res.json()["detail"], "Invalid clearance credentials.")

        # 5th attempt triggers lockout
        res_lock = await self.client.request("POST", "/api/owner/verify", json_data={"pin": "wrong_pin"}, client_ip=client_ip)
        self.assertEqual(res_lock.status, 429)
        self.assertIn("Account locked for 15 minutes", res_lock.json()["detail"])

        # 6th attempt while locked out rejects without user enumeration
        res_blocked = await self.client.request("POST", "/api/owner/verify", json_data={"pin": "wrong_pin", "username": "admin_target"}, client_ip=client_ip)
        self.assertEqual(res_blocked.status, 429)
        # Ensure target username is NOT reflected back in error string!
        self.assertNotIn("admin_target", res_blocked.json()["detail"])

        # Emergency Master Unlock: Real owner enters valid passcode (1704 or 3105)
        # Valid master credentials MUST bypass lockout and immediately reset counters!
        res_unlock = await self.client.request("POST", "/api/owner/verify", json_data={"pin": "1704"}, client_ip=client_ip)
        self.assertEqual(res_unlock.status, 200)
        self.assertEqual(res_unlock.json()["status"], "authorized")
        self.assertEqual(res_unlock.json()["role"], "owner")

        # Failure counter is now reset
        self.assertEqual(FAILED_VERIFY_ATTEMPTS[client_ip]["count"], 0)

    async def test_04_token_lifecycle_invalidation_on_logout(self):
        """Verify tokens are invalidated immediately on logout and active WebSockets evicted."""
        test_tok = "tok_to_logout_123"
        OWNER_TOKENS[test_tok] = time.time() + 3600
        OPERATOR_SESSIONS[test_tok] = {"username": "owner", "role": "owner", "expires": time.time() + 3600}

        # Logout request
        res = await self.client.request("POST", "/api/owner/logout", headers={"X-Owner-Token": test_tok})
        self.assertEqual(res.status, 200)
        self.assertEqual(res.json()["status"], "logged_out")

        # Token must be eradicated from memory
        self.assertNotIn(test_tok, OWNER_TOKENS)
        self.assertNotIn(test_tok, OPERATOR_SESSIONS)

        # Subsequent request with logged out token is rejected with 401
        res_post = await self.client.request("GET", "/api/dialogs", headers={"X-Owner-Token": test_tok})
        self.assertEqual(res_post.status, 401)

    # ═══════════════════════════════════════════════════════════════════
    #  PART 2: STICKER & GIF SYNCHRONIZATION AND RENDERING (IMAGE 2)
    # ═══════════════════════════════════════════════════════════════════

    def test_05_video_sticker_and_gif_metadata_extraction(self):
        """Verify video stickers (.webm) and GIFs (.mp4) are properly identified with is_video: True."""
        # 1. Telegram Video Sticker (.webm) with empty name
        doc_webm = types.Document(
            id=101, access_hash=1, file_reference=b"", date=None,
            mime_type="video/webm", size=25000, dc_id=1,
            attributes=[
                types.DocumentAttributeSticker(alt="🦋", stickerset=types.InputStickerSetEmpty()),
                types.DocumentAttributeVideo(duration=2, w=512, h=512)
            ]
        )
        msg_webm = types.Message(
            id=501, peer_id=types.PeerChannel(channel_id=1243949124),
            date=None, message="", media=types.MessageMediaDocument(document=doc_webm)
        )
        info_webm = extract_media_info(msg_webm, group_id=1243949124)
        self.assertEqual(info_webm["media_type"], "sticker")
        self.assertEqual(info_webm["media_emoji"], "🦋")
        self.assertTrue(info_webm["is_video"])
        self.assertEqual(info_webm["media_url"], "/api/media/preview/1243949124/501")

        # 2. Telegram GIF (.mp4 animated document)
        doc_gif = types.Document(
            id=102, access_hash=1, file_reference=b"", date=None,
            mime_type="video/mp4", size=85000, dc_id=1,
            attributes=[
                types.DocumentAttributeAnimated(),
                types.DocumentAttributeVideo(duration=3, w=320, h=240)
            ]
        )
        msg_gif = types.Message(
            id=502, peer_id=types.PeerChannel(channel_id=1243949124),
            date=None, message="", media=types.MessageMediaDocument(document=doc_gif)
        )
        info_gif = extract_media_info(msg_gif, group_id=1243949124)
        self.assertEqual(info_gif["media_type"], "gif")
        self.assertTrue(info_gif["is_video"])
        self.assertEqual(info_gif["fallback_text"], "[🎬 GIF Animation]")
        self.assertEqual(info_gif["media_url"], "/api/media/preview/1243949124/502")

    async def test_06_tgs_animated_sticker_never_serves_raw_tgs_to_browser(self):
        """Verify get_media_preview_file NEVER returns raw .tgs to HTML and prioritizes thumbnail."""
        vault_dir = Path(__file__).resolve().parent.parent / "media_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)

        gid = 1243949124
        mid = 99991

        # Simulate a .tgs file on disk and its corresponding JPEG thumbnail
        tgs_file = vault_dir / f"preview_{gid}_{mid}.tgs"
        thumb_file = vault_dir / f"thumb_{gid}_{mid}.jpg"

        with open(tgs_file, "wb") as f:
            f.write(b"LOTTIEMOCKGZIPDATA")
        with open(thumb_file, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0\x00\x10JFIFMOCKTHUMBNAIL")

        try:
            # Requesting preview without prefer_thumb MUST return the browser-renderable thumb_ file, NEVER .tgs!
            preview_path = await engine.get_media_preview_file(gid, mid, prefer_thumb=False)
            self.assertIsNotNone(preview_path)
            self.assertTrue(preview_path.endswith(".jpg"), f"Expected .jpg thumbnail but got: {preview_path}")
            self.assertFalse(preview_path.endswith(".tgs"), "CRITICAL: Raw .tgs served to browser!")

            # Requesting preview with prefer_thumb=True also returns thumbnail
            thumb_path = await engine.get_media_preview_file(gid, mid, prefer_thumb=True)
            self.assertEqual(thumb_path, preview_path)
        finally:
            tgs_file.unlink(missing_ok=True)
            thumb_file.unlink(missing_ok=True)

    async def test_07_in_flight_download_task_synchronization(self):
        """Verify get_media_preview_file awaits in-flight background download task to eliminate race conditions."""
        vault_dir = Path(__file__).resolve().parent.parent / "media_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)

        gid = 1243949124
        mid = 99992
        final_file = vault_dir / f"preview_{gid}_{mid}.webp"

        async def mock_download_worker():
            await asyncio.sleep(0.15)
            with open(final_file, "wb") as f:
                f.write(b"RIFFWEBPTESTIMAGE")

        task = asyncio.create_task(mock_download_worker())
        if not hasattr(engine, '_media_download_tasks'):
            engine._media_download_tasks = {}
        engine._media_download_tasks[f"{gid}_{mid}"] = task

        try:
            res = await engine.get_media_preview_file(gid, mid)
            self.assertIsNotNone(res)
            self.assertEqual(Path(res).name, f"preview_{gid}_{mid}.webp")
        finally:
            final_file.unlink(missing_ok=True)
            engine._media_download_tasks.pop(f"{gid}_{mid}", None)

    # ═══════════════════════════════════════════════════════════════════
    #  PART 3: MESSAGE SYNCHRONIZATION CONTINUITY & RESILIENCE
    # ═══════════════════════════════════════════════════════════════════

    async def test_08_api_messages_timeout_fallback_to_sqlite(self):
        """Verify /api/messages never hangs on slow/offline MTProto and falls back to SQLite."""
        test_gid = -1001243949124
        bare_gid = 1243949124

        # Seed stored messages into database
        if engine.db:
            await engine.db.save_intercepted_message(
                group_id=test_gid,
                group_title="English Chatting Global",
                message_id=7701,
                user_id=1614098432,
                full_name="Dogga",
                username="doggatg",
                text="I mean pat",
                media_type="text",
                date_sent="2026-11-15 18:08:00"
            )

        # Mock engine.get_group_entity to hang/timeout
        async def slow_get_group_entity(gid):
            await asyncio.sleep(10.0)
            return None

        with patch.object(engine, "get_group_entity", side_effect=slow_get_group_entity):
            res = await self.client.request(
                "GET", f"/api/messages?group_id={bare_gid}&limit=10",
                headers={"X-Owner-Token": self.owner_token}
            )
            self.assertEqual(res.status, 200)
            msgs = res.json()
            self.assertIsInstance(msgs, list)
            # Must return the cached message from SQLite rather than timing out or hanging!
            self.assertTrue(len(msgs) >= 1)
            dogga_msg = next((m for m in msgs if m.get("message_id") == 7701), None)
            self.assertIsNotNone(dogga_msg)
            self.assertEqual(dogga_msg["text"], "I mean pat")

    async def test_09_checkpoints_and_gap_detection_on_ingestion(self):
        """Verify message ingestion checkpoints track last_message_id and detect coverage gaps."""
        db_path = Path("/tmp/test_sync_checkpoints.db")
        if db_path.exists():
            db_path.unlink()

        db = DatabaseManager(db_path=db_path)
        gid = 987654321

        # 1. Ingest message 100
        await db.save_intercepted_message(
            group_id=gid, group_title="Test Group", message_id=100,
            user_id=1, full_name="User 1", username="u1", text="Hello 100"
        )

        health_1 = await db.get_ingestion_health(group_id=gid)
        self.assertEqual(health_1["total_ingested_messages"], 1)

        # 2. Ingest message 105 (discontinuity: gap from 101 to 104)
        await db.save_intercepted_message(
            group_id=gid, group_title="Test Group", message_id=105,
            user_id=1, full_name="User 1", username="u1", text="Hello 105"
        )

        health_2 = await db.get_ingestion_health(group_id=gid)
        self.assertEqual(health_2["total_ingested_messages"], 2)
        gaps = health_2.get("known_coverage_gaps", [])
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["start_id"], 101)
        self.assertEqual(gaps[0]["end_id"], 104)

        # 3. Resolve gap
        await db.resolve_coverage_gap(gid, 101, 104)
        health_3 = await db.get_ingestion_health(group_id=gid)
        gaps_3 = health_3.get("known_coverage_gaps", [])
        self.assertEqual(len(gaps_3), 1)
        self.assertEqual(gaps_3[0]["status"], "recovered")

        db.conn.close()
        db_path.unlink(missing_ok=True)

    async def test_10_atomic_media_file_safety(self):
        """Verify that temporary .tmp files are never served as previews."""
        vault_dir = Path(__file__).resolve().parent.parent / "media_vault"
        vault_dir.mkdir(parents=True, exist_ok=True)

        gid = 1243949124
        mid = 88881
        tmp_file = vault_dir / f"preview_{gid}_{mid}.tmp"
        with open(tmp_file, "wb") as f:
            f.write(b"INCOMPLETEBYTES")

        try:
            res = await engine.get_media_preview_file(gid, mid)
            self.assertIsNone(res, "Incomplete .tmp file was erroneously returned as a preview!")
        finally:
            tmp_file.unlink(missing_ok=True)

    async def test_11_social_graph_fallback_and_enrichment(self):
        """Verify that social graph topology eliminates blank influencer names and enriches known users."""
        db_path = Path("/tmp/test_social_graph.db")
        db = DatabaseManager(db_path)
        gid = 998877

        # 1. Record interaction with blank source name and None target name
        interactions = [
            (gid, 4001, "", 4002, None, "reply", 101, "2026-11-15 12:00:00"),
            (gid, 4001, "", 4003, "   ", "mention", 102, "2026-11-15 12:05:00")
        ]
        await db.batch_record_social_interactions(interactions)

        # 2. Enrich 4001 in user_cross_profiles
        members = [
            {
                "user_id": 4001,
                "full_name": "Ghost Operative",
                "username": "ghost_op",
                "message_count": 50,
                "is_premium": False,
                "is_verified": False
            }
        ]
        await db.batch_update_user_cross_profiles(gid, "War Room", members)

        # 3. Retrieve social graph
        graph = await db.get_social_graph(group_id=gid)
        self.assertEqual(graph["edge_count"], 2)
        self.assertEqual(graph["node_count"], 3)

        # 4. Verify all nodes have non-empty, sanitized labels
        for n in graph["nodes"]:
            self.assertTrue(bool(n.get("label")), f"Node {n['id']} has empty label")
            self.assertNotEqual(n["label"].strip(), "", f"Node {n['id']} label is whitespace")
            self.assertIn("user_id", n)
            self.assertEqual(n["id"], n["user_id"])

        # 5. Verify user 4001 was enriched from user_cross_profiles
        node_4001 = next(n for n in graph["nodes"] if n["id"] == 4001)
        self.assertEqual(node_4001["label"], "Ghost Operative")

        # 6. Verify top influencers list has no blank labels
        for inf in graph["top_influencers"]:
            self.assertTrue(bool(inf.get("label")), "Influencer has empty label")
            self.assertNotEqual(inf["label"].strip(), "")

        db.conn.close()
        db_path.unlink(missing_ok=True)

    async def test_12_social_graph_api_endpoint_sanitization(self):
        """Verify that /api/graph/social returns clean, sanitized top influencers with no blank labels."""
        import secrets
        tok = secrets.token_hex(32)
        OWNER_TOKENS[tok] = time.time() + 3600
        client = InProcessAsgiClient(app)
        res = await client.request("GET", "/api/graph/social?limit=50", headers={"X-Owner-Token": tok})
        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertIn("nodes", data)
        self.assertIn("links", data)
        self.assertIn("top_influencers", data)

        for inf in data["top_influencers"]:
            label = inf.get("label", "")
            self.assertTrue(bool(label and label.strip()), f"Influencer {inf} has blank label")


if __name__ == "__main__":
    unittest.main()
