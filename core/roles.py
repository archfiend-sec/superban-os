"""
KAGE // Sovereign Telegram Role Normalization & Detection Engine
Provides authoritative, centralized role mapping directly from Telegram API / MTProto contracts.
Eliminates false-positive ADMIN detections caused by voice chat volume flags or improper heuristics.
"""

from typing import Any, Dict, Optional, Union
import logging

try:
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
    )
    from telethon.tl.custom.participantpermissions import ParticipantPermissions
    HAS_TELETHON = True
except ImportError:
    HAS_TELETHON = False
    ChannelParticipant = ()
    ChannelParticipantCreator = ()
    ChannelParticipantAdmin = ()
    ChannelParticipantBanned = ()
    ChannelParticipantLeft = ()
    ChannelParticipantSelf = ()
    ChatParticipant = ()
    ChatParticipantCreator = ()
    ChatParticipantAdmin = ()
    ParticipantPermissions = ()

logger = logging.getLogger("KAGE.Roles")


class ParticipantRole:
    """Canonical Telegram participant roles according to authoritative Telegram specifications."""
    OWNER = "OWNER"            # Group Creator / Sovereign Owner
    ADMIN = "ADMIN"            # Promoted Administrator
    MEMBER = "MEMBER"          # Standard Verified Group Member
    RESTRICTED = "RESTRICTED"  # Member with permissions restricted (muted, no media, etc.)
    BANNED = "BANNED"          # Banned / Kicked from group
    LEFT = "LEFT"              # Left / No longer a participant
    UNVERIFIED = "UNVERIFIED"  # Status cannot be verified via Telegram API


def normalize_telegram_role(
    raw_obj: Any,
    custom_title: Optional[str] = None
) -> Dict[str, Any]:
    """
    Centralized role normalization function.
    
    Accepts:
      - MTProto ChannelParticipant / ChatParticipant instances
      - Telethon ParticipantPermissions objects
      - Telegram Bot API getChatMember dicts (e.g. {"status": "creator", ...})
      - String status identifiers ("creator", "administrator", "member", etc.)
      
    GUARANTEES:
      - ONLY assigns ADMIN when Telegram authoritative response confirms administrator status.
      - ONLY assigns OWNER when Telegram confirms creator status.
      - Never promotes an unknown or unverified participant to administrator.
      - Voice-chat volume flags, mic status, and activity scores are NEVER used for role inference.
    """
    role = ParticipantRole.UNVERIFIED
    is_owner = False
    is_admin = False
    is_restricted = False
    is_member = False
    is_verified = False
    extracted_title = custom_title or ""
    raw_type_name = type(raw_obj).__name__ if raw_obj is not None else "None"

    if raw_obj is None:
        return {
            "role": ParticipantRole.UNVERIFIED,
            "is_owner": False,
            "is_admin": False,
            "is_restricted": False,
            "is_member": False,
            "is_verified": False,
            "custom_title": "",
            "rank": "Unverified",
            "raw_type": "None"
        }

    # 1. Telethon / MTProto ChannelParticipant objects (Supergroups & Channels)
    if HAS_TELETHON and isinstance(raw_obj, ChannelParticipantCreator):
        role = ParticipantRole.OWNER
        is_owner = True
        is_admin = True
        is_verified = True
        extracted_title = getattr(raw_obj, 'rank', '') or extracted_title or "Owner"

    elif HAS_TELETHON and isinstance(raw_obj, ChannelParticipantAdmin):
        role = ParticipantRole.ADMIN
        is_owner = False
        is_admin = True
        is_verified = True
        extracted_title = getattr(raw_obj, 'rank', '') or extracted_title or "Admin"

    elif HAS_TELETHON and isinstance(raw_obj, ChannelParticipantBanned):
        is_verified = True
        banned_rights = getattr(raw_obj, 'banned_rights', None)
        if banned_rights and getattr(banned_rights, 'view_messages', False):
            role = ParticipantRole.BANNED
        else:
            role = ParticipantRole.RESTRICTED
            is_restricted = True

    elif HAS_TELETHON and isinstance(raw_obj, ChannelParticipantLeft):
        role = ParticipantRole.LEFT
        is_verified = True

    elif HAS_TELETHON and isinstance(raw_obj, ChannelParticipantSelf):
        # Self participant in channel
        is_verified = True
        if getattr(raw_obj, 'is_creator', False):
            role = ParticipantRole.OWNER
            is_owner = True
            is_admin = True
        elif getattr(raw_obj, 'admin_rights', None):
            role = ParticipantRole.ADMIN
            is_admin = True
        else:
            role = ParticipantRole.MEMBER
            is_member = True

    elif HAS_TELETHON and isinstance(raw_obj, ChannelParticipant):
        role = ParticipantRole.MEMBER
        is_member = True
        is_verified = True

    # 2. Telethon / MTProto ChatParticipant objects (Basic Telegram Groups)
    elif HAS_TELETHON and isinstance(raw_obj, ChatParticipantCreator):
        role = ParticipantRole.OWNER
        is_owner = True
        is_admin = True
        is_verified = True
        extracted_title = extracted_title or "Owner"

    elif HAS_TELETHON and isinstance(raw_obj, ChatParticipantAdmin):
        role = ParticipantRole.ADMIN
        is_owner = False
        is_admin = True
        is_verified = True
        extracted_title = extracted_title or "Admin"

    elif HAS_TELETHON and isinstance(raw_obj, ChatParticipant):
        role = ParticipantRole.MEMBER
        is_member = True
        is_verified = True

    # 3. Telethon ParticipantPermissions custom wrapper
    elif HAS_TELETHON and isinstance(raw_obj, ParticipantPermissions):
        is_verified = True
        if raw_obj.is_creator:
            role = ParticipantRole.OWNER
            is_owner = True
            is_admin = True
            extracted_title = extracted_title or "Owner"
        elif raw_obj.is_admin:
            role = ParticipantRole.ADMIN
            is_owner = False
            is_admin = True
            extracted_title = extracted_title or "Admin"
        elif raw_obj.is_banned:
            role = ParticipantRole.RESTRICTED
            is_restricted = True
        elif raw_obj.has_left:
            role = ParticipantRole.LEFT
        elif raw_obj.has_default_permissions:
            role = ParticipantRole.MEMBER
            is_member = True
        else:
            role = ParticipantRole.MEMBER
            is_member = True

    # 4. Telegram Bot API ChatMember dictionary (from getChatMember)
    elif isinstance(raw_obj, dict):
        status = str(raw_obj.get("status", "")).lower()
        extracted_title = raw_obj.get("custom_title", "") or extracted_title
        if status in ("creator", "owner"):
            role = ParticipantRole.OWNER
            is_owner = True
            is_admin = True
            is_verified = True
            extracted_title = extracted_title or "Owner"
        elif status == "administrator":
            role = ParticipantRole.ADMIN
            is_owner = False
            is_admin = True
            is_verified = True
            extracted_title = extracted_title or "Admin"
        elif status == "member":
            role = ParticipantRole.MEMBER
            is_member = True
            is_verified = True
        elif status == "restricted":
            role = ParticipantRole.RESTRICTED
            is_restricted = True
            is_verified = True
        elif status == "left":
            role = ParticipantRole.LEFT
            is_verified = True
        elif status in ("kicked", "banned"):
            role = ParticipantRole.BANNED
            is_verified = True
        else:
            role = ParticipantRole.UNVERIFIED
            is_verified = False

    # 5. String status representations
    elif isinstance(raw_obj, str):
        s = raw_obj.strip().lower()
        if s in ("creator", "owner"):
            role = ParticipantRole.OWNER
            is_owner = True
            is_admin = True
            is_verified = True
        elif s in ("administrator", "admin"):
            role = ParticipantRole.ADMIN
            is_owner = False
            is_admin = True
            is_verified = True
        elif s == "member":
            role = ParticipantRole.MEMBER
            is_member = True
            is_verified = True
        elif s == "restricted":
            role = ParticipantRole.RESTRICTED
            is_restricted = True
            is_verified = True
        elif s == "left":
            role = ParticipantRole.LEFT
            is_verified = True
        elif s in ("kicked", "banned"):
            role = ParticipantRole.BANNED
            is_verified = True
        elif s in ("unverified", "unknown"):
            role = ParticipantRole.UNVERIFIED
            is_verified = False
        else:
            role = ParticipantRole.UNVERIFIED
            is_verified = False

    # 6. Fallback if object contains participant sub-attribute
    elif (
        not isinstance(raw_obj, (dict, str))
        and type(raw_obj).__name__ not in ("MagicMock", "Mock", "AsyncMock")
        and hasattr(raw_obj, 'participant')
    ):
        p = getattr(raw_obj, 'participant', None)
        if p is not None and type(p).__name__ not in ("MagicMock", "Mock", "AsyncMock"):
            return normalize_telegram_role(p, custom_title=custom_title)

    return {
        "role": role,
        "is_owner": is_owner,
        "is_admin": is_admin,
        "is_restricted": is_restricted,
        "is_member": is_member,
        "is_verified": is_verified,
        "custom_title": extracted_title,
        "rank": extracted_title or role.title(),
        "raw_type": raw_type_name
    }


def format_role_badge(role_info: Dict[str, Any]) -> Dict[str, str]:
    """
    Format authoritative role information into UI badge descriptor.
    """
    role = role_info.get("role", ParticipantRole.UNVERIFIED)
    title = role_info.get("custom_title", "")
    
    if role == ParticipantRole.OWNER:
        label = f"👑 OWNER [{title}]" if title and title != "Owner" else "👑 OWNER"
        return {"role": role, "label": label, "color": "#f59e0b", "css": "badge-owner", "emoji": "👑"}
    elif role == ParticipantRole.ADMIN:
        label = f"🛡️ ADMIN [{title}]" if title and title != "Admin" else "🛡️ ADMIN"
        return {"role": role, "label": label, "color": "#ef4444", "css": "badge-ember", "emoji": "🛡️"}
    elif role == ParticipantRole.RESTRICTED:
        return {"role": role, "label": "🚫 RESTRICTED", "color": "#dc2626", "css": "badge-vermilion", "emoji": "🚫"}
    elif role == ParticipantRole.BANNED:
        return {"role": role, "label": "⛔ BANNED", "color": "#991b1b", "css": "badge-vermilion", "emoji": "⛔"}
    elif role == ParticipantRole.LEFT:
        return {"role": role, "label": "🚪 LEFT", "color": "#6b7280", "css": "badge-muted", "emoji": "🚪"}
    elif role == ParticipantRole.UNVERIFIED:
        return {"role": role, "label": "❓ UNVERIFIED", "color": "#9ca3af", "css": "badge-muted", "emoji": "❓"}
    else:
        return {"role": ParticipantRole.MEMBER, "label": "MEMBER", "color": "#64748b", "css": "badge-muted", "emoji": ""}
