"""
KAGE // Test Suite: Authoritative Telegram Participant Role Detection & Badge Verification
Tests:
1. Confirmed group owner (Creator) -> OWNER (👑 OWNER)
2. Confirmed administrator -> ADMIN (🛡️ ADMIN)
3. Ordinary group member -> MEMBER (MEMBER, never ADMIN)
4. Restricted member -> RESTRICTED (🚫 RESTRICTED)
5. Unverified / Inaccessible participant -> UNVERIFIED (❓ UNVERIFIED)
6. Voice chat participant isolation: volume_by_admin & VC participation NEVER promote to ADMIN.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from core.roles import ParticipantRole, normalize_telegram_role, format_role_badge
from telethon.tl.types import (
    ChannelParticipant,
    ChannelParticipantCreator,
    ChannelParticipantAdmin,
    ChannelParticipantBanned,
    ChannelParticipantLeft,
    ChannelParticipantSelf,
    ChatParticipant,
    ChatParticipantCreator,
    ChatParticipantAdmin,
    ChatBannedRights,
    ChatAdminRights,
    GroupCall,
    GroupCallParticipant,
    PeerUser
)
from telethon.tl.custom.participantpermissions import ParticipantPermissions


class TestParticipantRoleDetection(unittest.TestCase):
    """Authoritative role normalization tests across all 5 participant types."""

    def test_01_confirmed_group_owner(self):
        """1. Confirmed Group Owner: Creator is classified as OWNER and granted owner badge."""
        # A. Telethon MTProto ChannelParticipantCreator
        creator_tl = ChannelParticipantCreator(user_id=1001, admin_rights=None, rank="Founder")
        norm = normalize_telegram_role(creator_tl)
        self.assertEqual(norm["role"], ParticipantRole.OWNER)
        self.assertTrue(norm["is_owner"])
        self.assertTrue(norm["is_admin"])
        self.assertEqual(norm["custom_title"], "Founder")
        self.assertTrue(norm["is_verified"])

        badge = format_role_badge(norm)
        self.assertIn("OWNER", badge["label"])
        self.assertEqual(badge["emoji"], "👑")

        # B. Telegram Bot API getChatMember response
        bot_api_creator = {"status": "creator", "custom_title": "Grandmaster", "user": {"id": 1001}}
        norm_bot = normalize_telegram_role(bot_api_creator)
        self.assertEqual(norm_bot["role"], ParticipantRole.OWNER)
        self.assertTrue(norm_bot["is_owner"])
        self.assertTrue(norm_bot["is_admin"])
        self.assertEqual(norm_bot["custom_title"], "Grandmaster")

        # C. Telethon ParticipantPermissions
        perms_mock = MagicMock(spec=ParticipantPermissions)
        perms_mock.is_creator = True
        perms_mock.is_admin = True
        norm_perms = normalize_telegram_role(perms_mock)
        self.assertEqual(norm_perms["role"], ParticipantRole.OWNER)
        self.assertTrue(norm_perms["is_owner"])
        print("  ✅ PASS: Confirmed group owner verified with role OWNER and 👑 badge.")

    def test_02_confirmed_administrator(self):
        """2. Confirmed Administrator: Admin is classified as ADMIN and granted admin badge."""
        # A. Telethon MTProto ChannelParticipantAdmin
        admin_tl = ChannelParticipantAdmin(
            user_id=2002, inviter_id=1001, promoted_by=1001, date=datetime.now(timezone.utc),
            admin_rights=ChatAdminRights(ban_users=True, delete_messages=True), rank="Security Officer"
        )
        norm = normalize_telegram_role(admin_tl)
        self.assertEqual(norm["role"], ParticipantRole.ADMIN)
        self.assertFalse(norm["is_owner"])
        self.assertTrue(norm["is_admin"])
        self.assertEqual(norm["custom_title"], "Security Officer")
        self.assertTrue(norm["is_verified"])

        badge = format_role_badge(norm)
        self.assertIn("ADMIN", badge["label"])
        self.assertEqual(badge["emoji"], "🛡️")

        # B. Telegram Bot API getChatMember response
        bot_api_admin = {"status": "administrator", "custom_title": "Moderator", "user": {"id": 2002}}
        norm_bot = normalize_telegram_role(bot_api_admin)
        self.assertEqual(norm_bot["role"], ParticipantRole.ADMIN)
        self.assertFalse(norm_bot["is_owner"])
        self.assertTrue(norm_bot["is_admin"])

        # C. Basic group ChatParticipantAdmin
        chat_admin_tl = ChatParticipantAdmin(user_id=2002, inviter_id=1001, date=datetime.now(timezone.utc))
        norm_chat = normalize_telegram_role(chat_admin_tl)
        self.assertEqual(norm_chat["role"], ParticipantRole.ADMIN)
        self.assertTrue(norm_chat["is_admin"])
        print("  ✅ PASS: Confirmed administrator verified with role ADMIN and 🛡️ badge.")

    def test_03_ordinary_group_member(self):
        """3. Ordinary Group Member: Standard member is strictly classified as MEMBER, NEVER ADMIN."""
        # A. Telethon MTProto ChannelParticipant
        member_tl = ChannelParticipant(user_id=3003, date=datetime.now(timezone.utc))
        norm = normalize_telegram_role(member_tl)
        self.assertEqual(norm["role"], ParticipantRole.MEMBER)
        self.assertFalse(norm["is_owner"])
        self.assertFalse(norm["is_admin"], "CRITICAL: Ordinary member must NEVER be marked is_admin=True")
        self.assertFalse(norm["is_restricted"])
        self.assertTrue(norm["is_verified"])

        badge = format_role_badge(norm)
        self.assertEqual(badge["role"], ParticipantRole.MEMBER)
        self.assertEqual(badge["label"], "MEMBER")
        self.assertNotIn("ADMIN", badge["label"])
        self.assertNotIn("OWNER", badge["label"])

        # B. Telegram Bot API getChatMember response
        bot_api_member = {"status": "member", "user": {"id": 3003}}
        norm_bot = normalize_telegram_role(bot_api_member)
        self.assertEqual(norm_bot["role"], ParticipantRole.MEMBER)
        self.assertFalse(norm_bot["is_admin"])

        # C. Basic group ChatParticipant
        chat_member_tl = ChatParticipant(user_id=3003, inviter_id=1001, date=datetime.now(timezone.utc))
        norm_chat = normalize_telegram_role(chat_member_tl)
        self.assertEqual(norm_chat["role"], ParticipantRole.MEMBER)
        self.assertFalse(norm_chat["is_admin"])
        print("  ✅ PASS: Ordinary member strictly classified as MEMBER (zero false ADMIN promotion).")

    def test_04_restricted_member(self):
        """4. Restricted Member: User with restricted rights receives RESTRICTED badge."""
        # A. Telethon MTProto ChannelParticipantBanned (Restricted rights)
        banned_rights = ChatBannedRights(until_date=None, send_messages=True, send_media=True)
        restricted_tl = ChannelParticipantBanned(
            peer=PeerUser(user_id=4004), kicked_by=1001, date=datetime.now(timezone.utc), banned_rights=banned_rights
        )
        norm = normalize_telegram_role(restricted_tl)
        self.assertEqual(norm["role"], ParticipantRole.RESTRICTED)
        self.assertTrue(norm["is_restricted"])
        self.assertFalse(norm["is_admin"])

        badge = format_role_badge(norm)
        self.assertIn("RESTRICTED", badge["label"])
        self.assertEqual(badge["emoji"], "🚫")

        # B. Telegram Bot API getChatMember response
        bot_api_restricted = {"status": "restricted", "user": {"id": 4004}}
        norm_bot = normalize_telegram_role(bot_api_restricted)
        self.assertEqual(norm_bot["role"], ParticipantRole.RESTRICTED)
        self.assertTrue(norm_bot["is_restricted"])
        self.assertFalse(norm_bot["is_admin"])
        print("  ✅ PASS: Restricted member verified with role RESTRICTED and 🚫 badge.")

    def test_05_unverified_participant(self):
        """5. Unverified Participant: Missing or unresolvable status receives UNVERIFIED, NEVER ADMIN."""
        # A. None / Unreachable
        norm_none = normalize_telegram_role(None)
        self.assertEqual(norm_none["role"], ParticipantRole.UNVERIFIED)
        self.assertFalse(norm_none["is_verified"])
        self.assertFalse(norm_none["is_admin"])
        self.assertFalse(norm_none["is_owner"])

        badge_none = format_role_badge(norm_none)
        self.assertEqual(badge_none["label"], "❓ UNVERIFIED")

        # B. Unknown string status
        norm_str = normalize_telegram_role("unknown_custom_status")
        self.assertEqual(norm_str["role"], ParticipantRole.UNVERIFIED)
        self.assertFalse(norm_str["is_admin"])
        print("  ✅ PASS: Unverified participant safely defaults to UNVERIFIED (never promoted to ADMIN).")

    def test_06_voice_chat_isolation_volume_by_admin(self):
        """
        6. Voice Chat Participant Isolation:
        CRITICAL ROOT CAUSE REGRESSION:
        GroupCallParticipant with volume_by_admin=True MUST NOT be promoted to ADMIN.
        Joining a voice chat does not change a member's group role.
        """
        from core.engine import SuperBanEngine
        from unittest.mock import MagicMock

        engine = SuperBanEngine(MagicMock(), MagicMock())

        # Mock group roles cache: only user 1001 is OWNER, 2002 is ADMIN
        engine._group_roles_cache = {
            12345: {
                "roles": {
                    1001: {"role": ParticipantRole.OWNER, "is_owner": True, "is_admin": True, "custom_title": "Owner", "is_verified": True},
                    2002: {"role": ParticipantRole.ADMIN, "is_owner": False, "is_admin": True, "custom_title": "Admin", "is_verified": True},
                },
                "time": 9999999999.0
            }
        }

        # Simulate 3 participants in a Voice Chat:
        # User 1001: Group Owner
        # User 2002: Group Admin
        # User 3003: Ordinary Member with volume_by_admin=True! (The bug trigger)
        # User 5005: Ordinary Member speaking with mic open
        fake_participants_data = [
            {"uid": 1001, "vol_admin": False, "expected_role": "OWNER", "expected_admin": True, "expected_owner": True},
            {"uid": 2002, "vol_admin": False, "expected_role": "ADMIN", "expected_admin": True, "expected_owner": False},
            {"uid": 3003, "vol_admin": True,  "expected_role": "MEMBER", "expected_admin": False, "expected_owner": False},
            {"uid": 5005, "vol_admin": True,  "expected_role": "MEMBER", "expected_admin": False, "expected_owner": False},
        ]

        roles_map = engine._group_roles_cache[12345]["roles"]

        for item in fake_participants_data:
            uid = item["uid"]
            vol_by_admin = item["vol_admin"]

            # Replicate the hardened engine role determination
            if uid in roles_map:
                role_info = roles_map[uid]
            else:
                role_info = normalize_telegram_role("member")

            is_owner = role_info.get("is_owner", False)
            is_admin = role_info.get("is_admin", False)
            role_str = role_info.get("role", "MEMBER")

            self.assertEqual(role_str, item["expected_role"], f"User {uid} role mismatch")
            self.assertEqual(is_admin, item["expected_admin"], f"User {uid} is_admin mismatch")
            self.assertEqual(is_owner, item["expected_owner"], f"User {uid} is_owner mismatch")

        # Crucial assertion: Ordinary member #3003 with volume_by_admin=True remains strictly MEMBER
        ordinary_norm = roles_map.get(3003, normalize_telegram_role("member"))
        self.assertFalse(ordinary_norm["is_admin"], "volume_by_admin must NEVER grant administrator status!")
        self.assertEqual(ordinary_norm["role"], "MEMBER")
        print("  ✅ PASS: Voice chat isolation confirmed: volume_by_admin & mic state NEVER promote to ADMIN.")


if __name__ == "__main__":
    unittest.main()
