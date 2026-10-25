import asyncio
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("MediaExtractor")

def extract_media_info(msg, group_id: int = 0) -> Dict[str, Any]:
    """Extract media type, emoji, file attributes, and preview URLs from Telethon message or event."""
    info = {
        "has_media": False,
        "media_type": None,
        "media_emoji": "",
        "file_name": "",
        "file_size": 0,
        "mime_type": "",
        "media_url": "",
        "fallback_text": ""
    }
    if not msg:
        return info

    media = getattr(msg, 'media', None)
    if not media:
        return info

    info["has_media"] = True
    f = getattr(msg, 'file', None)
    if f:
        info["media_emoji"] = getattr(f, 'emoji', '') or ''
        info["file_name"] = getattr(f, 'name', '') or ''
        info["file_size"] = getattr(f, 'size', 0) or 0
        info["mime_type"] = getattr(f, 'mime_type', '') or ''

    if getattr(msg, 'sticker', None):
        info["media_type"] = "sticker"
        emoji_disp = f" {info['media_emoji']}" if info['media_emoji'] else ""
        info["fallback_text"] = f"[🎨 Sticker{emoji_disp}]"
    elif getattr(msg, 'gif', None):
        info["media_type"] = "gif"
        info["fallback_text"] = "[🎬 GIF Animation]"
    elif getattr(msg, 'video_note', None):
        info["media_type"] = "video_note"
        info["fallback_text"] = "[⭕ Round Video Note]"
    elif getattr(msg, 'video', None):
        info["media_type"] = "video"
        info["fallback_text"] = "[📹 Video]"
    elif getattr(msg, 'photo', None):
        info["media_type"] = "photo"
        info["fallback_text"] = "[🖼️ Photo]"
    elif getattr(msg, 'voice', None):
        info["media_type"] = "voice"
        info["fallback_text"] = "[🎙️ Voice Message]"
    elif getattr(msg, 'audio', None):
        info["media_type"] = "audio"
        info["fallback_text"] = f"[🎵 Audio: {info['file_name'] or 'Track'}]"
    elif getattr(msg, 'poll', None):
        info["media_type"] = "poll"
        poll_obj = getattr(getattr(msg, 'poll', None), 'poll', None)
        question = getattr(poll_obj, 'question', 'Poll') if poll_obj else 'Poll'
        if hasattr(question, 'text'):
            question = question.text
        info["fallback_text"] = f"[📊 Poll: {question}]"
    elif getattr(msg, 'document', None):
        info["media_type"] = "document"
        info["fallback_text"] = f"[📄 Document: {info['file_name'] or 'File'}]"
    elif getattr(msg, 'contact', None):
        info["media_type"] = "contact"
        info["fallback_text"] = "[👤 Contact Card]"
    elif getattr(msg, 'geo', None) or getattr(msg, 'venue', None):
        info["media_type"] = "location"
        info["fallback_text"] = "[📍 Location]"
    else:
        info["media_type"] = "media"
        info["fallback_text"] = "[📎 Media Attachment]"

    msg_id = getattr(msg, 'id', 0)
    gid = abs(int(group_id)) if group_id else 0
    if gid and msg_id:
        info["media_url"] = f"/api/media/preview/{gid}/{msg_id}"

    return info


async def extract_reply_info(msg, msg_cache: Optional[Dict[int, Any]] = None) -> Optional[Dict[str, Any]]:
    """Extract tagged / replied-to message context and sender details."""
    if not msg:
        return None

    is_reply = bool(getattr(msg, 'is_reply', False))
    reply_to_id = getattr(msg, 'reply_to_msg_id', None)
    if not is_reply or not reply_to_id:
        return None

    reply_info = {
        "reply_to_msg_id": reply_to_id,
        "reply_to_sender": "",
        "reply_to_text": "",
        "reply_to_media_type": None
    }

    # 1. Quote text if directly provided in Telegram 2.0+ MessageReplyHeader
    reply_header = getattr(msg, 'reply_to', None)
    quote_text = getattr(reply_header, 'quote_text', None)
    if quote_text:
        reply_info["reply_to_text"] = quote_text

    # 2. Check in-memory msg_cache first
    orig_msg = None
    if msg_cache and reply_to_id in msg_cache:
        orig_msg = msg_cache[reply_to_id]

    # 3. If not cached, fetch via get_reply_message() with a strict timeout
    if not orig_msg and hasattr(msg, 'get_reply_message'):
        try:
            orig_msg = await asyncio.wait_for(msg.get_reply_message(), timeout=1.5)
            if msg_cache is not None and orig_msg:
                msg_cache[reply_to_id] = orig_msg
        except Exception:
            pass

    if orig_msg:
        try:
            r_sender = await orig_msg.get_sender()
            r_first = getattr(r_sender, 'first_name', '') or ''
            r_last = getattr(r_sender, 'last_name', '') or ''
            reply_info["reply_to_sender"] = f"{r_first} {r_last}".strip() or getattr(r_sender, 'title', '') or getattr(r_sender, 'username', '') or "User"
        except Exception:
            reply_info["reply_to_sender"] = "User"

        orig_raw = getattr(orig_msg, 'raw_text', None)
        if not isinstance(orig_raw, str):
            orig_raw = getattr(orig_msg, 'message', None)
        if not isinstance(orig_raw, str):
            orig_raw = getattr(orig_msg, 'text', None)
        r_text = orig_raw if isinstance(orig_raw, str) else ""
        if not r_text and getattr(orig_msg, 'media', None):
            r_media = extract_media_info(orig_msg)
            r_text = r_media.get("fallback_text", "[Attachment]")
            reply_info["reply_to_media_type"] = r_media.get("media_type")

        if not reply_info["reply_to_text"]:
            reply_info["reply_to_text"] = r_text
    else:
        if not reply_info["reply_to_sender"]:
            reply_info["reply_to_sender"] = "Replied Message"
        if not reply_info["reply_to_text"]:
            reply_info["reply_to_text"] = f"Message #{reply_to_id}"

    return reply_info


def extract_reactions_from_obj(reactions_obj) -> Dict[str, Any]:
    """Extract emoji reactions and counts from a Telethon MessageReactions object."""
    reactions = []
    recent_reactors = []
    if not reactions_obj:
        return {"reactions": reactions, "recent_reactors": recent_reactors}

    results = getattr(reactions_obj, 'results', None) or []
    for r in results:
        reaction = getattr(r, 'reaction', None)
        count = getattr(r, 'count', 0)
        emoticon = ""
        if hasattr(reaction, 'emoticon'):
            emoticon = reaction.emoticon
        elif hasattr(reaction, 'document_id'):
            emoticon = "✨"
        if emoticon and count > 0:
            reactions.append({
                "emoji": emoticon,
                "count": count
            })

    # Recent reactors (if present on message object)
    recent = getattr(reactions_obj, 'recent_reactions', None) or []
    for item in recent:
        peer = getattr(item, 'peer_id', None)
        user_id = getattr(peer, 'user_id', None) if peer else None
        reaction = getattr(item, 'reaction', None)
        emoji = getattr(reaction, 'emoticon', '') if reaction else ''
        if user_id:
            recent_reactors.append({
                "user_id": user_id,
                "emoji": emoji
            })

    return {
        "reactions": reactions,
        "recent_reactors": recent_reactors
    }


def extract_reactions_info(msg) -> Dict[str, Any]:
    """Extract reactions and recent reactors from a Telethon message or event."""
    if not msg:
        return {"reactions": [], "recent_reactors": []}
    reactions_obj = getattr(msg, 'reactions', None)
    return extract_reactions_from_obj(reactions_obj)
