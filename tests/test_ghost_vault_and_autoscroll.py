import unittest
import asyncio
import os
import shutil
import tempfile
from core.db import DatabaseManager

class TestGhostVaultAndInterception(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_kage.db")
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.db = DatabaseManager(self.db_path)

    def tearDown(self):
        self.db.close()
        self.loop.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_ghost_vault_edit_and_polymorphic_groups(self):
        """Verify message edits record original and new text, support polymorphic IDs, and return complete dicts."""
        gid = -1002077123456
        mid = 998811

        # Intercept an initial message
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid,
            group_title="Elite Command Cyber",
            message_id=mid,
            user_id=445566,
            full_name="Agent Zero",
            username="agent0",
            text="Initial secret transmission"
        ))

        # Check interception is stored
        initial_ghost = self.loop.run_until_complete(self.db.get_ghost_message_by_id(mid))
        self.assertIsNotNone(initial_ghost)
        self.assertEqual(initial_ghost["text"], "Initial secret transmission")
        self.assertFalse(initial_ghost["is_edited"])

        # Edit using positive variant group ID (e.g. Telethon channel peer format 2077123456)
        positive_gid = 2077123456
        edit_record = self.loop.run_until_complete(self.db.mark_message_edited(
            group_id=positive_gid,
            message_id=mid,
            new_text="Edited sanitized disclosure"
        ))

        self.assertIsNotNone(edit_record)
        self.assertEqual(edit_record["original_text"], "Initial secret transmission")
        self.assertEqual(edit_record["new_text"], "Edited sanitized disclosure")
        self.assertEqual(edit_record["text"], "Edited sanitized disclosure")
        self.assertTrue(edit_record["is_edited"])

        # Retrieve ghost message by message_id
        ghost_by_mid = self.loop.run_until_complete(self.db.get_ghost_message_by_id(mid))
        self.assertIsNotNone(ghost_by_mid)
        self.assertEqual(ghost_by_mid["original_text"], "Initial secret transmission")
        self.assertEqual(ghost_by_mid["new_text"], "Edited sanitized disclosure")
        self.assertEqual(ghost_by_mid["message_text"], "Edited sanitized disclosure")

        # Test ghost overlay batch lookup
        overlay = self.loop.run_until_complete(self.db.get_ghost_overlay_for_messages(gid, [mid, 999999]))
        self.assertIn(mid, overlay)
        self.assertTrue(overlay[mid]["is_edited"])
        self.assertEqual(overlay[mid]["original_text"], "Initial secret transmission")
        self.assertEqual(overlay[mid]["new_text"], "Edited sanitized disclosure")

    def test_ghost_vault_deletion_and_recovery(self):
        """Verify deleted messages are captured with complete metadata and preserved locally."""
        gid = -1001999888777
        mid = 776655

        # Intercept message
        self.loop.run_until_complete(self.db.save_intercepted_message(
            group_id=gid,
            group_title="Cyber Threat Cell",
            message_id=mid,
            user_id=112233,
            full_name="Target Whistleblower",
            username="whistleblower",
            text="Classified document leaked link: https://pastebin.com/xyz"
        ))

        # Delete message with None group_id fallback (sentinel fallback when Telegram omits chat_id)
        deleted_records = self.loop.run_until_complete(self.db.mark_messages_deleted(
            group_id=None,
            message_ids=[mid]
        ))

        self.assertEqual(len(deleted_records), 1)
        del_rec = deleted_records[0]
        self.assertTrue(del_rec["is_deleted"])
        self.assertEqual(del_rec["original_text"], "Classified document leaked link: https://pastebin.com/xyz")
        self.assertEqual(del_rec["sender_name"], "Target Whistleblower")
        self.assertIn("deleted_at", del_rec)

        # Confirm Ghost Vault list returns it
        ghost_list = self.loop.run_until_complete(self.db.get_ghost_messages(gid, filter_mode="deleted"))
        self.assertEqual(len(ghost_list), 1)
        self.assertEqual(ghost_list[0]["message_id"], mid)
        self.assertTrue(ghost_list[0]["is_deleted"])
        self.assertEqual(ghost_list[0]["message_text"], "Classified document leaked link: https://pastebin.com/xyz")

if __name__ == '__main__':
    unittest.main()
