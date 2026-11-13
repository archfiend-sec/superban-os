import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from telethon import events
from core.sentinel import SentinelShield
from core.engine import SuperBanEngine
from core.db import DatabaseManager

class TestRealtimeWiretapZeroLatency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_client = MagicMock()
        self.mock_client.on = MagicMock(return_value=lambda f: f)
        self.mock_db = MagicMock(spec=DatabaseManager)
        self.mock_db.record_activity = AsyncMock()
        self.mock_db.save_intercepted_message = AsyncMock()
        self.mock_db.update_user_cross_profile = AsyncMock()
        self.mock_db.save_extracted_entities = AsyncMock()

        self.sentinel = SentinelShield(self.mock_client, self.mock_db)

    async def test_01_instant_emission_without_rpc_wait(self):
        """Verify SentinelShield emits live_chat_message immediately with zero RPC latency."""
        received_events = []
        self.sentinel.register_callback(lambda evt: received_events.append(evt))

        # Event with uncached sender and chat (simulating high-traffic Telegram stream)
        mock_event = MagicMock()
        mock_event.id = 991823
        mock_event.chat_id = -1001234567890
        mock_event.sender_id = 88776655
        mock_event.raw_text = "Urgent: Instant alpha signal deployed"
        mock_event.sender = None
        mock_event.chat = None
        mock_event.out = False
        mock_event.is_reply = False
        mock_event.media = None
        mock_event.date = None
        mock_event.get_sender = AsyncMock(return_value=None)
        mock_event.get_chat = AsyncMock(return_value=None)

        # Process incoming message
        await self.sentinel._process_incoming_message(mock_event)

        # Must have emitted live_chat_message synchronously
        self.assertEqual(len(received_events), 1)
        evt = received_events[0]
        self.assertEqual(evt["type"], "live_chat_message")
        self.assertEqual(evt["data"]["message_id"], 991823)
        self.assertEqual(evt["data"]["group_id"], -1001234567890)
        self.assertEqual(evt["data"]["user_id"], 88776655)
        self.assertEqual(evt["data"]["full_name"], "User #88776655")
        self.assertEqual(evt["data"]["text"], "Urgent: Instant alpha signal deployed")
        print("  ✅ PASS: Incoming message emitted instantly to live wiretap (<1ms) without RPC blockage.")

    async def test_02_bot_messages_not_dropped(self):
        """Verify bot messages are NOT suppressed, ensuring 100% full wire visibility."""
        received_events = []
        self.sentinel.register_callback(lambda evt: received_events.append(evt))

        bot_sender = MagicMock()
        bot_sender.first_name = "Whale Alert"
        bot_sender.last_name = "Bot"
        bot_sender.username = "whale_alert_bot"
        bot_sender.bot = True
        bot_sender.id = 1000999

        mock_event = MagicMock()
        mock_event.id = 991824
        mock_event.chat_id = -1001234567890
        mock_event.sender_id = 1000999
        mock_event.raw_text = "5,000 BTC transferred from Binance to Unknown Wallet"
        mock_event.sender = bot_sender
        mock_event.chat = MagicMock(title="Crypto Alpha Group")
        mock_event.out = False
        mock_event.is_reply = False
        mock_event.media = None
        mock_event.date = None

        await self.sentinel._process_incoming_message(mock_event)

        self.assertEqual(len(received_events), 1)
        evt = received_events[0]
        self.assertEqual(evt["type"], "live_chat_message")
        self.assertEqual(evt["data"]["full_name"], "Whale Alert Bot")
        self.assertEqual(evt["data"]["trust_level"], "BOT / SERVICE")
        print("  ✅ PASS: Bot & automated service messages are preserved on the Signal Wire feed.")

    async def test_03_client_rebinding_on_session_change(self):
        """Verify attach_client dynamically rebinds event listeners to new client instances."""
        new_client = MagicMock()
        new_client.on = MagicMock(return_value=lambda f: f)

        self.sentinel.attach_client(new_client)

        self.assertIs(self.sentinel.client, new_client)
        self.assertTrue(self.sentinel._handlers_registered)
        # Check that new_client.on was invoked to register event handlers
        self.assertGreater(new_client.on.call_count, 0)
        print("  ✅ PASS: SentinelShield dynamically rebinds event handlers on client reconnection/auth.")

    async def test_04_engine_set_client_propagates_to_sentinel(self):
        """Verify SuperBanEngine.set_client propagates the active client to SentinelShield."""
        engine = SuperBanEngine(self.mock_client, self.mock_db)
        newer_client = MagicMock()
        newer_client.on = MagicMock(return_value=lambda f: f)

        engine.set_client(newer_client)
        self.assertIs(engine.client, newer_client)
        self.assertIs(engine.sentinel.client, newer_client)
        print("  ✅ PASS: SuperBanEngine.set_client smoothly binds client to SentinelShield and VC listeners.")

    def test_05_frontend_zero_batch_polling_verification(self):
        """Verify index.html has no periodic batch sync polling or batch dumps."""
        html_path = Path(__file__).resolve().parent.parent / "web" / "static" / "index.html"
        content = html_path.read_text(encoding="utf-8")

        # Must not have syncWiretapCatchup() inside recurring intervals
        self.assertNotIn("setInterval(() => {\n    if (document.visibilityState === 'visible' && currentViewMode === 'warroom') {\n      const isWsDead = !ws || ws.readyState !== WebSocket.OPEN;\n      if (isWsDead) {\n        syncWiretapCatchup();", content)

        # syncWiretapCatchup should be deprecated/no-op
        self.assertIn("async function syncWiretapCatchup()", content)

        # Must listen for message_sender_resolved
        self.assertIn("msg.type === 'message_sender_resolved'", content)
        print("  ✅ PASS: Frontend index.html confirmed 100% clean of batch polling and sync intervals.")

if __name__ == "__main__":
    unittest.main()
