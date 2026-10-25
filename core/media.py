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
