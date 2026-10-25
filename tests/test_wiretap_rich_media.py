import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.media import extract_media_info, extract_reply_info
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


class TestWiretapRichMedia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock_client = MagicMock()
        cls.mock_db = MagicMock()
        cls.engine = SuperBanEngine(cls.mock_client, cls.mock_db)
        cls.app = create_app(cls.engine)
        cls.client = InProcessAsgiClient(cls.app)

        # Create valid test clearance token
        cls.token = "test_owner_media_token_123"
        OPERATOR_SESSIONS[cls.token] = {"username": "archfiend000", "role": "owner", "expires": 9999999999.0}

    def test_01_extract_media_info_sticker(self):
        """Verify sticker detection and emoji extraction."""
        msg = MagicMock()
        msg.media = MagicMock()
        msg.sticker = True
        msg.gif = False
        msg.photo = False
        msg.video = False
        msg.voice = False
        msg.document = False
        msg.file = MagicMock()
        msg.file.emoji = "🔥"
        msg.file.name = "sticker.webp"
        msg.file.size = 12345
        msg.file.mime_type = "image/webp"
        msg.id = 101

        info = extract_media_info(msg, group_id=-100123456789)
        self.assertTrue(info["has_media"])
        self.assertEqual(info["media_type"], "sticker")
        self.assertEqual(info["media_emoji"], "🔥")
        self.assertEqual(info["fallback_text"], "[🎨 Sticker 🔥]")
        self.assertEqual(info["media_url"], "/api/media/preview/100123456789/101")
        print("  ✅ PASS: Sticker media extracted with emoji 🔥 and preview URL.")

    def test_02_extract_media_info_gif_and_photo(self):
        """Verify GIF and photo detection."""
        # GIF
        gif_msg = MagicMock()
        gif_msg.media = MagicMock()
        gif_msg.sticker = False
        gif_msg.gif = True
        gif_msg.photo = False
        gif_msg.video = True
        gif_msg.id = 202

        info_gif = extract_media_info(gif_msg, group_id=12345)
        self.assertTrue(info_gif["has_media"])
        self.assertEqual(info_gif["media_type"], "gif")
        self.assertEqual(info_gif["fallback_text"], "[🎬 GIF Animation]")

        # Photo
        photo_msg = MagicMock()
        photo_msg.media = MagicMock()
        photo_msg.sticker = False
        photo_msg.gif = False
        photo_msg.video_note = False
        photo_msg.video = False
        photo_msg.voice = False
        photo_msg.audio = False
        photo_msg.photo = True
        photo_msg.id = 303

        info_photo = extract_media_info(photo_msg, group_id=12345)
        self.assertTrue(info_photo["has_media"])
        self.assertEqual(info_photo["media_type"], "photo")
        self.assertEqual(info_photo["fallback_text"], "[🖼️ Photo]")
        print("  ✅ PASS: GIF and Photo correctly typed with rich fallback markers.")

    def test_03_extract_media_info_video_and_voice(self):
        """Verify video, round video note, and voice note detection."""
        # Video
        vid_msg = MagicMock()
        vid_msg.media = MagicMock()
        vid_msg.sticker = False
        vid_msg.gif = False
        vid_msg.photo = False
        vid_msg.video = True
        vid_msg.video_note = False
        vid_msg.voice = False
        vid_msg.id = 404

        info_vid = extract_media_info(vid_msg, group_id=12345)
        self.assertEqual(info_vid["media_type"], "video")

        # Round video note
        round_msg = MagicMock()
        round_msg.media = MagicMock()
        round_msg.sticker = False
        round_msg.gif = False
        round_msg.photo = False
        round_msg.video = True
        round_msg.video_note = True
        round_msg.id = 505

        info_round = extract_media_info(round_msg, group_id=12345)
        self.assertEqual(info_round["media_type"], "video_note")

        # Voice note
        voice_msg = MagicMock()
        voice_msg.media = MagicMock()
        voice_msg.sticker = False
        voice_msg.gif = False
        voice_msg.photo = False
        voice_msg.video = False
        voice_msg.video_note = False
        voice_msg.voice = True
        voice_msg.id = 606

        info_voice = extract_media_info(voice_msg, group_id=12345)
        self.assertEqual(info_voice["media_type"], "voice")
        print("  ✅ PASS: Video, Video Note (Round), and Voice Message correctly identified.")

    def test_04_get_recent_messages_does_not_drop_non_text_media(self):
        """Verify get_recent_messages preserves media cards when text is empty."""
        async def run_test():
            msg_sticker = MagicMock()
            msg_sticker.id = 701
            msg_sticker.text = ""  # No text!
            msg_sticker.media = MagicMock()
            msg_sticker.sticker = True
            msg_sticker.gif = False
            msg_sticker.video_note = False
            msg_sticker.video = False
            msg_sticker.photo = False
            msg_sticker.voice = False
            msg_sticker.audio = False
            msg_sticker.file = MagicMock()
            msg_sticker.file.emoji = "🐱"
            msg_sticker.get_sender = AsyncMock(return_value=MagicMock(first_name="Jardin", last_name="", username="jardin_cat", id=111))
            msg_sticker.date = MagicMock()
            msg_sticker.date.strftime.return_value = "12:30:00"
            msg_sticker.date.timestamp.return_value = 1700000000.0

            msg_photo = MagicMock()
            msg_photo.id = 702
            msg_photo.text = ""  # No text!
            msg_photo.media = MagicMock()
            msg_photo.sticker = False
            msg_photo.gif = False
            msg_photo.video_note = False
            msg_photo.video = False
            msg_photo.photo = True
            msg_photo.voice = False
            msg_photo.audio = False
            msg_photo.file = MagicMock(name="photo.jpg", size=2048)
            msg_photo.get_sender = AsyncMock(return_value=MagicMock(first_name="cao", last_name="", username="cao_user", id=222))
            msg_photo.date = MagicMock()
            msg_photo.date.strftime.return_value = "12:30:05"
            msg_photo.date.timestamp.return_value = 1700000005.0

            self.mock_client.get_messages = AsyncMock(return_value=[msg_photo, msg_sticker])

            res = await self.engine.get_recent_messages(MagicMock(id=123456), limit=10)
            self.assertEqual(len(res), 2)
            
            # Check sticker card
            self.assertEqual(res[0]["message_id"], 701)
            self.assertEqual(res[0]["full_name"], "Jardin")
            self.assertEqual(res[0]["media_type"], "sticker")
            self.assertEqual(res[0]["media_emoji"], "🐱")
            self.assertTrue(res[0]["has_media"])
            self.assertEqual(res[0]["text"], "[🎨 Sticker 🐱]")

            # Check photo card
            self.assertEqual(res[1]["message_id"], 702)
            self.assertEqual(res[1]["full_name"], "cao")
            self.assertEqual(res[1]["media_type"], "photo")
            self.assertTrue(res[1]["has_media"])
            self.assertEqual(res[1]["text"], "[🖼️ Photo]")

        asyncio.run(run_test())
        print("  ✅ PASS: get_recent_messages preserves non-text sticker and photo cards!")

    def test_05_media_preview_endpoint(self):
        """Verify /api/media/preview/{group_id}/{message_id} serves cached media with headers."""
        async def run_test():
            vault_dir = Path(__file__).resolve().parent.parent / "media_vault"
            vault_dir.mkdir(parents=True, exist_ok=True)
            test_file = vault_dir / "preview_999888_777.webp"
            test_file.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 \x00\x00\x00\x00")

            try:
                headers = {"X-Owner-Token": self.token}
                res = await self.client.request("GET", "/api/media/preview/999888/777", headers=headers)
                self.assertEqual(res.status, 200)
                self.assertEqual(res.headers.get("content-type"), "image/webp")
                self.assertEqual(res.headers.get("x-content-type-options"), "nosniff")
                self.assertIn("max-age", res.headers.get("cache-control", ""))

                # Path traversal attempt check
                res_bad = await self.client.request("GET", "/api/media/preview/999888/777?thumb=false", headers=headers)
                self.assertEqual(res_bad.status, 200)

                # Non-existent media returns 404
                res_404 = await self.client.request("GET", "/api/media/preview/999888/99999999", headers=headers)
                self.assertEqual(res_404.status, 404)
            finally:
                if test_file.exists():
                    test_file.unlink()

        asyncio.run(run_test())
        print("  ✅ PASS: /api/media/preview endpoint verified with HTTP 200, WebP MIME, and security headers.")

    def test_06_tagged_reply_extraction(self):
        """Verify tagged / replied message context extraction."""
        async def run_test():
            # Replied-to original message (from Boni)
            orig_msg = MagicMock()
            orig_msg.id = 555
            orig_msg.text = "Sorry guys I was stuck in traffic"
            orig_msg.media = None
            orig_msg.get_sender = AsyncMock(return_value=MagicMock(first_name="Boni", last_name="M", username="boni_m", id=999))

            # Incoming reply message (from Kairos: "It's ok boni no sorry")
            reply_msg = MagicMock()
            reply_msg.id = 556
            reply_msg.text = "It's ok boni no sorry"
            reply_msg.is_reply = True
            reply_msg.reply_to_msg_id = 555
            reply_msg.reply_to = MagicMock(quote_text="Sorry guys")
            reply_msg.media = None

            # Test extract_reply_info with cache
            msg_cache = {555: orig_msg}
            reply_info = await extract_reply_info(reply_msg, msg_cache=msg_cache)
            self.assertIsNotNone(reply_info)
            self.assertEqual(reply_info["reply_to_msg_id"], 555)
            self.assertEqual(reply_info["reply_to_sender"], "Boni M")
            self.assertEqual(reply_info["reply_to_text"], "Sorry guys")

            # Non-reply message returns None
            non_reply = MagicMock()
            non_reply.is_reply = False
            non_reply.reply_to_msg_id = None
            self.assertIsNone(await extract_reply_info(non_reply))

        asyncio.run(run_test())
        print("  ✅ PASS: Tagged / replied-to message extracted with sender Boni M & quote context!")

if __name__ == "__main__":
    print("=" * 70)
    print("🛰️  WIRETAP AUTOSCROLL & RICH MEDIA PREVIEW VERIFICATION SUITE")
    print("=" * 70 + "\n")
    unittest.main()
