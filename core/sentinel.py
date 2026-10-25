import asyncio
import time
import logging
import re
import hashlib
import html
from pathlib import Path
from collections import defaultdict, deque
from typing import Dict, Any, Optional, Callable, List, Set

from telethon import TelegramClient, events
from telethon.tl import types
from telethon.tl.functions.channels import EditBannedRequest
from telethon.tl.functions.messages import EditChatDefaultBannedRightsRequest
from telethon.tl.types import ChatBannedRights
from telethon.errors import RPCError

from core.forensics import ForensicInspector
from core.config import Config
from core.media import extract_media_info, extract_reply_info, extract_reactions_info, extract_reactions_from_obj

logger = logging.getLogger("SentinelShield")

SPAM_LINK_REGEX = re.compile(r'(https?://[^\s]+|t\.me/(\+|joinchat/|[a-zA-Z0-9_]+))', re.IGNORECASE)
CRYPTO_SCAM_REGEX = re.compile(r'(airdrop|giveaway|presale|claim free|private sale|send eth|deposit|doubler|guaranteed profit|\bseed phrase\b|\bpermit2\b|\bphantom\b|\bmetamask\b|\bwallet connect\b)', re.IGNORECASE)
ZERO_DAY_REGEX = re.compile(r'(bit\.ly|tinyurl\.com|rb\.gy|is\.gd|is\.gd|cutt\.ly|grabify|iplogger|t\.m[e\u0435])', re.IGNORECASE)
IMPERSONATION_KEYWORDS = ['admin', 'support', 'helpdesk', 'moderator', 'official', 'announcement', 'service']

# Signal & IOC Extraction Radar Expressions
SOL_REGEX = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')
ETH_REGEX = re.compile(r'\b0x[a-fA-F0-9]{40}\b')
BTC_REGEX = re.compile(r'\b(?:bc1[a-zA-HJ-NP-Z0-9]{25,39}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})\b')
TON_REGEX = re.compile(r'\b(?:EQ|UQ)[0-9a-zA-Z_-]{46}\b')
TG_INVITE_REGEX = re.compile(r'(?:https?:\/\/)?(?:www\.)?t\.me\/(?:\+|joinchat\/)[a-zA-Z0-9_-]+')
CASHTAG_REGEX = re.compile(r'\$([A-Za-z0-9]{2,10})\b')
URL_REGEX = re.compile(r'https?:\/\/[^\s<>"]+')
EMAIL_REGEX = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b')

class SentinelShield:
    """Autonomous Real-Time Defensive Guardian for Telegram Groups."""

    def __init__(self, client: TelegramClient, db, engine=None):
        self.client = client
        self.db = db
        self.engine = engine
        self.active_groups: Dict[int, Dict[str, Any]] = {}
        self.recent_joins: Dict[int, deque] = defaultdict(lambda: deque(maxlen=30))
        
        self.stats = {
            "spammers_blocked_on_join": 0,
            "spam_messages_nuked": 0,
            "raids_deflected": 0,
            "active_shields": 0
        }
        
        self.event_callbacks: List[Callable[[Dict[str, Any]], Any]] = []
        self._handlers_registered = False
        self._init_handlers()

    def register_callback(self, cb: Callable[[Dict[str, Any]], Any]):
        self.event_callbacks.append(cb)

    async def emit_sentinel_event(self, event_type: str, data: Dict[str, Any]):
        payload = {"type": event_type, "data": data, "timestamp": time.time()}
        for cb in self.event_callbacks:
            try:
                res = cb(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    def _init_handlers(self):
        if self._handlers_registered:
            return

        @self.client.on(events.ChatAction)
        async def on_chat_action(event):
            group_id = event.chat_id

            if event.user_joined or event.user_added or event.user_left:
                try:
                    user = await event.get_user()
                    u_name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip() or "User"
                    action_type = "joined" if (event.user_joined or event.user_added) else "left"
                    chat = await event.get_chat()
                    group_title = getattr(chat, 'title', '') or ''
                    await self.emit_sentinel_event("live_chat_action", {
                        "group_id": group_id,
                        "group_title": group_title,
                        "user_id": user.id if user else 0,
                        "action": action_type,
                        "full_name": u_name,
                        "username": getattr(user, 'username', '') if user else '',
                        "date": time.strftime("%H:%M:%S", time.gmtime()),
                        "timestamp": time.time()
                    })
                except Exception:
                    pass

            if group_id not in self.active_groups:
                return

            opts = self.active_groups[group_id]
            if event.user_joined or event.user_added:
                user = await event.get_user()
                if not user:
                    return

                now = time.time()
                self.recent_joins[group_id].append(now)

                # Raid Detection: >5 joins within 4 seconds
                joins_in_window = [t for t in self.recent_joins[group_id] if now - t <= 4.0]
                if len(joins_in_window) >= opts.get("raid_threshold", 5):
                    logger.critical(f"🚨 RAID DETECTED in group {group_id}! Auto-lockdown engaging.")
                    self.stats["raids_deflected"] += 1
                    await self.emit_sentinel_event("raid_detected", {
                        "group_id": group_id, "joins_in_window": len(joins_in_window)
                    })
                    if opts.get("auto_lockdown_on_raid", True):
                        await self.lockdown_group(group_id)

                # Auto-Bouncer: evaluate user profile on join
                if opts.get("auto_ban_scammers", True):
                    profile = ForensicInspector.inspect_user(user)
                    if profile["is_suspicious"] or profile["is_deleted"] or profile["is_fake"]:
                        try:
                            rights = ChatBannedRights(until_date=None, view_messages=True)
                            await self.client(EditBannedRequest(channel=event.chat, participant=user.id, banned_rights=rights))
                            self.stats["spammers_blocked_on_join"] += 1
                            logger.info(f"🛡️ Sentinel auto-banned scammer on join: {user.id} ({profile['full_name']})")
                            await self.emit_sentinel_event("spammer_intercepted", {
                                "group_id": group_id, "user": profile, "reasons": profile["reasons"]
                            })
                        except Exception as e:
                            logger.debug(f"Sentinel bouncer failed: {e}")

        @self.client.on(events.NewMessage)
        async def on_new_message(event):
            group_id = event.chat_id
            
            # Record activity for analytics heatmap
            now_dt = time.localtime()
            if self.db:
                asyncio.create_task(self.db.record_activity(group_id, now_dt.tm_hour, now_dt.tm_wday))

            sender = await event.get_sender()
            if not sender or getattr(sender, 'bot', False):
                return

            text = event.raw_text or ""
            is_threat = False
            threat_type = "NORMAL"
            trigger_reason = ""
            confidence = 0

            # Pre-evaluate threat categories
            if ZERO_DAY_REGEX.search(text):
                is_threat = True
                threat_type = "ZERO_DAY_PHISH"
                trigger_reason = "Homoglyph / Obfuscated shortlink phishing detected"
                confidence = 98
            elif CRYPTO_SCAM_REGEX.search(text):
                is_threat = True
                threat_type = "CRYPTO_DRAINER"
                trigger_reason = "Airdrop / Wallet drainer signature matched"
                confidence = 95
            elif SPAM_LINK_REGEX.search(text):
                is_threat = True
                threat_type = "UNAUTHORIZED_INVITE"
                trigger_reason = "Unauthorized channel invite / external link"
                confidence = 92

            # Always emit live wiretap event to connected clients
            first_name = getattr(sender, 'first_name', '') or ''
            last_name = getattr(sender, 'last_name', '') or ''
            full_name = f"{first_name} {last_name}".strip() or "Anonymous"
            username = getattr(sender, 'username', '') or ''
            
            group_title = ""
            try:
                chat = await event.get_chat()
                group_title = getattr(chat, 'title', '') or ''
            except Exception:
                pass

            # Sovereign Intelligence: Intercept message into Ghost Vault & Update Cross-Group OSINT
            extracted_entities = self._extract_entities(text, group_id, group_title, event.id, sender.id, full_name)
            
            # Plan B: Ghost Media Vault preservation (intercept media <= 15MB before deletion)
            media_path = ""
            file_name = ""
            file_size = 0
            file_hash = ""
            if event.media:
                try:
                    f_size = getattr(getattr(event, 'file', None), 'size', 0)
                    if f_size and f_size <= 15 * 1024 * 1024:
                        vault_dir = Path(__file__).resolve().parent.parent / "media_vault"
                        vault_dir.mkdir(parents=True, exist_ok=True)
                        ext = getattr(getattr(event, 'file', None), 'ext', '') or '.bin'
                        clean_ext = ext if ext.startswith('.') else f".{ext}"
                        file_name = f"ghost_{abs(group_id)}_{event.id}{clean_ext}"
                        target_file = vault_dir / file_name
                        downloaded = await event.download_media(file=str(target_file))
                        if downloaded and Path(downloaded).exists():
                            media_path = str(downloaded)
                            file_size = Path(downloaded).stat().st_size
                            hasher = hashlib.sha256()
                            with open(downloaded, "rb") as mf:
                                while chunk := mf.read(65536):
                                    hasher.update(chunk)
                            file_hash = hasher.hexdigest()
                except Exception as me:
                    logger.debug(f"Ghost media vault download note: {me}")

            # Extract rich media metadata (stickers, gifs, photos, videos, voice)
            media_info = extract_media_info(getattr(event, 'message', None) or event, group_id=group_id)
            display_text = text or media_info.get("fallback_text", "")
            if media_info["file_name"] and not file_name:
                file_name = media_info["file_name"]

            # Extract tagged / replied-to message context
            reply_info = await extract_reply_info(getattr(event, 'message', None) or event)
            reactions_info = extract_reactions_info(getattr(event, 'message', None) or event)

            if self.db:
                asyncio.create_task(self.db.save_intercepted_message(
                    group_id, group_title, event.id, sender.id, full_name, username, display_text,
                    media_info.get("media_type") or ("media" if event.media else "text"),
                    media_path, file_name, file_size, file_hash
                ))
                asyncio.create_task(self.db.update_user_cross_profile(
                    sender.id, full_name, username, group_id, group_title,
                    bool(getattr(sender, 'premium', False)), bool(getattr(sender, 'verified', False))
                ))
                asyncio.create_task(self._check_surveillance(
                    group_id, group_title, sender.id, full_name, username, text
                ))
                if extracted_entities:
                    asyncio.create_task(self.db.save_extracted_entities(extracted_entities))
                    asyncio.create_task(self.emit_sentinel_event("entity_discovered", {
                        "group_id": group_id,
                        "group_title": group_title,
                        "sender_id": sender.id,
                        "sender_name": full_name,
                        "entities": extracted_entities,
                        "timestamp": time.strftime("%H:%M:%S", time.localtime())
                    }))
                asyncio.create_task(self._track_interaction(event, group_id, sender.id, full_name, text))
                asyncio.create_task(self._check_siphon(
                    group_id, group_title, sender.id, full_name, username, text, event.id, extracted_entities
                ))
            
            # Evaluate user forensic reputation in real-time
            user_profile = ForensicInspector.inspect_user(sender)

            msg_dt = getattr(event, 'date', None) or getattr(getattr(event, 'message', None), 'date', None)
            msg_ts = msg_dt.timestamp() if msg_dt else time.time()
            msg_date_str = msg_dt.strftime("%H:%M:%S") if msg_dt else time.strftime("%H:%M:%S", time.gmtime())

            await self.emit_sentinel_event("live_chat_message", {
                "message_id": event.id,
                "group_id": group_id,
                "group_title": group_title,
                "user_id": sender.id,
                "full_name": full_name,
                "username": username,
                "text": display_text,
                "raw_text": text,
                "date": msg_date_str,
                "timestamp": msg_ts,
                "is_threat": is_threat,
                "threat_type": threat_type,
                "confidence": confidence,
                "reason": trigger_reason,
                "trust_score": user_profile.get("trust_score", 70),
                "trust_level": user_profile.get("trust_level", "NEUTRAL CITIZEN"),
                "trust_color": user_profile.get("trust_color", "#38bdf8"),
                "vintage": user_profile.get("vintage", ""),
                "is_premium": bool(getattr(sender, 'premium', False)),
                "is_verified": bool(getattr(sender, 'verified', False)),
                "has_media": media_info["has_media"],
                "media_type": media_info["media_type"],
                "media_emoji": media_info["media_emoji"],
                "file_name": file_name or media_info["file_name"],
                "media_url": media_info["media_url"],
                "is_reply": bool(reply_info),
                "reply_to_msg_id": reply_info["reply_to_msg_id"] if reply_info else None,
                "reply_to_sender": reply_info["reply_to_sender"] if reply_info else None,
                "reply_to_text": reply_info["reply_to_text"] if reply_info else None,
                "reply_to_media_type": reply_info["reply_to_media_type"] if reply_info else None,
                "reactions": reactions_info.get("reactions", []),
                "recent_reactors": reactions_info.get("recent_reactors", [])
            })

            if group_id not in self.active_groups:
                return

            opts = self.active_groups[group_id]
            defcon = opts.get("defcon", 5)

            # DEFCON 1: NUCLEAR LOCKDOWN
            if defcon == 1:
                try:
                    await event.delete()
                    return
                except Exception:
                    pass

            # Threat Category 1: Zero-Day Phishing & Homoglyphs
            if ZERO_DAY_REGEX.search(text):
                is_threat = True
                threat_type = "ZERO_DAY_PHISH"
                trigger_reason = "Homoglyph / Obfuscated shortlink phishing detected"
                confidence = 98

            # Threat Category 2: Crypto Wallet Drainers & Presales
            elif CRYPTO_SCAM_REGEX.search(text):
                is_threat = True
                threat_type = "CRYPTO_DRAINER"
                trigger_reason = "Airdrop / Wallet drainer signature matched"
                confidence = 95

            # Threat Category 3: Unauthorized Invites (DEFCON 2+ blocks all links)
            elif SPAM_LINK_REGEX.search(text):
                if defcon <= 3 or opts.get("anti_spam_messages", True):
                    is_threat = True
                    threat_type = "UNAUTHORIZED_INVITE"
                    trigger_reason = "Unauthorized channel invite / external link"
                    confidence = 92

            # Threat Category 4: Impersonator & Admin Spoofing
            sender_display = f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')} {getattr(sender, 'username', '')}".lower()
            if any(k in sender_display for k in IMPERSONATION_KEYWORDS):
                # Verify if user is actually admin
                is_admin = False
                try:
                    perms = await self.client.get_permissions(event.chat, sender)
                    if perms and perms.is_admin:
                        is_admin = True
                except Exception:
                    pass
                if not is_admin:
                    is_threat = True
                    threat_type = "IMPERSONATOR"
                    trigger_reason = f"Impersonation keyword in profile: {sender_display}"
                    confidence = 96

            if is_threat:
                action_taken = "FLAGGED"
                try:
                    # Attempt admin deletion if privileged
                    await event.delete()
                    self.stats["spam_messages_nuked"] += 1
                    action_taken = "DELETED"

                    if opts.get("ban_message_spammers", True) or defcon <= 3:
                        rights = ChatBannedRights(until_date=None, view_messages=True)
                        await self.client(EditBannedRequest(channel=event.chat, participant=sender.id, banned_rights=rights))
                        action_taken = "BANNED"
                except Exception as del_err:
                    # Sovereign Non-Admin Defense Fallback:
                    # When lacking group admin rights, mark message publicly with warning emoji reaction
                    action_taken = "FLAGGED_AUTOREACT"
                    try:
                        from telethon.tl.functions.messages import SendReactionRequest
                        from telethon.tl.types import ReactionEmoji
                        await self.client(SendReactionRequest(peer=event.chat, msg_id=event.id, reaction=[ReactionEmoji(emoticon="🤡")]))
                        logger.info(f"🤡 Non-admin reaction dropped on threat msg #{event.id} in group {group_id}")
                    except Exception as react_err:
                        logger.debug(f"Threat reaction notice: {react_err}")

                try:
                    logger.warning(f"🚨 Neural Threat [{threat_type}] intercepted from {sender.id}: {trigger_reason} -> {action_taken}")
                    await self.db.log_ai_threat(
                        group_id, sender.id, getattr(sender, 'username', ''),
                        threat_type, confidence, trigger_reason, action_taken
                    )
                    await self.emit_sentinel_event("ai_threat_neutralized", {
                        "group_id": group_id, "user_id": sender.id,
                        "threat_type": threat_type, "confidence": confidence,
                        "reason": trigger_reason, "action": action_taken, "text": text[:120]
                    })

                    # High-confidence drainer alert sent directly to Saved Messages
                    if confidence >= 95 and is_threat:
                        try:
                            alert_card = (
                                f"🚨 <b>[SUPERBAN OS // THREAT RADAR]</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━\n"
                                f"👥 <b>Group:</b> {group_title} (<code>{group_id}</code>)\n"
                                f"👤 <b>Author:</b> {full_name} (@{username} / <code>{sender.id}</code>)\n"
                                f"⚡ <b>Detected Threat:</b> <code>{threat_type}</code> ({confidence}%)\n"
                                f"🔍 <b>Trigger:</b> <i>{trigger_reason}</i>\n"
                                f"🎯 <b>Action:</b> <code>{action_taken}</code>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━\n"
                                f"<blockquote>{text[:200]}</blockquote>"
                            )
                            await self.client.send_message('me', alert_card, parse_mode='html')
                        except Exception:
                            pass

                        # Plan H: Autonomous MTProto Report directly to Telegram Trust & Safety
                        if hasattr(self, 'engine') and self.engine:
                            try:
                                asyncio.create_task(self.engine.report_threat_to_telegram(
                                    group_id, event.id, reason="spam",
                                    details=f"Automated threat neutralization: {threat_type} (confidence: {confidence}%)"
                                ))
                                await self.emit_sentinel_event("threat_reported_to_telegram", {
                                    "group_id": group_id, "message_id": event.id, "threat_type": threat_type, "confidence": confidence
                                })
                            except Exception as rep_err:
                                logger.debug(f"Auto MTProto report note: {rep_err}")
                except Exception as e:
                    logger.debug(f"Failed to record threat: {e}")

        @self.client.on(events.MessageDeleted)
        async def on_message_deleted(event):
            group_id = event.chat_id
            deleted_ids = getattr(event, 'deleted_ids', [])
            if self.db and deleted_ids:
                try:
                    records = await self.db.mark_messages_deleted(group_id, deleted_ids)
                    if records:
                        logger.info(f"👻 Ghost Vault intercepted {len(records)} deleted messages in group {group_id}!")
                        await self.emit_sentinel_event("ghost_messages_deleted", {
                            "group_id": group_id,
                            "count": len(records),
                            "records": records,
                            "timestamp": time.strftime("%H:%M:%S", time.localtime())
                        })
                        for rec in records:
                            asyncio.create_task(self._check_deleted_siphon(rec))
                except Exception as e:
                    logger.debug(f"Ghost Vault delete hook note: {e}")

        @self.client.on(events.MessageEdited)
        async def on_message_edited(event):
            group_id = event.chat_id
            if self.db and event.id and event.raw_text:
                try:
                    record = await self.db.mark_message_edited(group_id, event.id, event.raw_text)
                    if record:
                        logger.info(f"👻 Ghost Vault intercepted edited message #{event.id} in group {group_id}!")
                        await self.emit_sentinel_event("ghost_message_edited", {
                            "group_id": group_id,
                            "record": record,
                            "timestamp": time.strftime("%H:%M:%S", time.localtime())
                        })
                except Exception as e:
                    logger.debug(f"Ghost Vault edit hook note: {e}")

        @self.client.on(events.Raw(types.UpdateMessageReactions))
        async def on_message_reactions_updated(update):
            """Real-time reaction telemetry dispatched to live wiretap HUD."""
            try:
                peer = getattr(update, 'peer', None)
                msg_id = getattr(update, 'msg_id', 0)
                reactions_obj = getattr(update, 'reactions', None)
                if not peer or not msg_id:
                    return

                group_id = 0
                if hasattr(peer, 'channel_id'):
                    group_id = -1000000000000 - peer.channel_id if peer.channel_id > 0 else peer.channel_id
                elif hasattr(peer, 'chat_id'):
                    group_id = -peer.chat_id
                elif hasattr(peer, 'user_id'):
                    group_id = peer.user_id

                rx_info = extract_reactions_from_obj(reactions_obj)
                await self.emit_sentinel_event("message_reactions_updated", {
                    "group_id": group_id,
                    "message_id": msg_id,
                    "reactions": rx_info.get("reactions", []),
                    "recent_reactors": rx_info.get("recent_reactors", []),
                    "timestamp": time.strftime("%H:%M:%S", time.localtime())
                })
            except Exception as e:
                logger.debug(f"Reaction update hook notice: {e}")

        # ═══════════════════════════════════════════════════════════════
        #   PLAN I: TELEGRAM COMPANION CONTROLLER FOR @archfiend000
        # ═══════════════════════════════════════════════════════════════
        @self.client.on(events.NewMessage)
        async def on_private_dm_companion(event):
            """Plan I: Non-Admin Remote Control in Telegram DM for Master Owner @archfiend000."""
            # Fast check: Process private DMs only (positive chat_id, is_private, or PeerUser)
            is_dm = bool(
                getattr(event, 'is_private', False) or
                (getattr(event, 'chat_id', None) is not None and event.chat_id > 0) or
                isinstance(getattr(event, 'peer_id', None), types.PeerUser) or
                isinstance(getattr(getattr(event, 'message', None), 'peer_id', None), types.PeerUser)
            )
            if not is_dm:
                return

            raw_text = (event.raw_text or "").strip()
            if not raw_text.startswith('/'):
                return

            owner_id = Config.get_owner_telegram_id()  # 8312795263
            owner_user = Config.get_owner_username()    # "archfiend000"

            async def reply_safe(text: str):
                try:
                    await event.reply(text, parse_mode='html')
                except Exception as ex_rep:
                    logger.warning(f"event.reply failed ({ex_rep}), falling back to direct send_message")
                    target = event.chat_id or event.sender_id or owner_id or owner_user
                    try:
                        await self.client.send_message(target, text, parse_mode='html')
                    except Exception as ex_send:
                        logger.error(f"Failed to send companion reply to {target}: {ex_send}")

            try:
                sender_id = event.sender_id or event.chat_id

                is_owner = False
                # Check 1: Cryptographic MTProto sender/chat ID match for Master Owner
                if sender_id and sender_id == owner_id:
                    is_owner = True
                elif event.chat_id and event.chat_id == owner_id:
                    is_owner = True

                # Check 2: Check self-chat or Saved Messages ('me')
                if not is_owner:
                    try:
                        me = await self.client.get_me()
                        me_id = getattr(me, 'id', None)
                        if me_id and (sender_id == me_id or event.chat_id == me_id):
                            is_owner = True
                    except Exception:
                        pass

                # Check 3: Check username
                sender_username = ""
                if not is_owner:
                    try:
                        sender = await event.get_sender()
                        if sender:
                            sender_username = (getattr(sender, 'username', '') or '').strip().lstrip('@').lower()
                            if sender_username == owner_user:
                                is_owner = True
                    except Exception as ex_ent:
                        logger.debug(f"Could not resolve sender entity: {ex_ent}")

                # Strict Owner Gatekeeper
                if not is_owner:
                    logger.warning(f"Unauthorized DM command from sender_id={sender_id}, username={sender_username}: {raw_text}")
                    await reply_safe(
                        "⛔ <b>SUPERBAN OS // ACCESS DENIED</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "This companion terminal is restricted strictly to <b>Master Owner @archfiend000</b>."
                    )
                    return

                parts = raw_text.split()
                cmd = parts[0].lower().split('@')[0]

                if cmd in ['/start', '/help']:
                    help_msg = (
                        "👑 <b>SUPERBAN OS // COMPANION COMMAND CENTER</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "Welcome, Master Owner <b>@archfiend000</b>. You have full non-admin remote control:\n\n"
                        "• 📊 <code>/status</code> — Live War Room telemetry, monitored groups & stats\n"
                        "• 🔑 <code>/assign &lt;@user&gt; &lt;4-digit-code&gt;</code> — Grant new operator clearance\n"
                        "• 🚫 <code>/revoke &lt;@user&gt;</code> — Terminate operator clearance instantly\n"
                        "• 👥 <code>/operators</code> — List active operator credentials & roles\n"
                        "• 📡 <code>/radar</code> — Latest high-yield crypto alpha & drainer alerts\n"
                        "• 👻 <code>/ghost</code> — Intercepted deleted messages vault overview\n"
                        "• 🎙️ <code>/vc</code> — Live voice chat tracking & talker telemetry\n"
                        "• 🚨 <code>/report &lt;group_id&gt; &lt;msg_id&gt; [reason]</code> — 1-Click MTProto report to TG T&S\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "🔒 <i>Zero group admin privileges required. Operating with supreme MTProto capabilities.</i>"
                    )
                    await reply_safe(help_msg)

                elif cmd == '/status':
                    active_grp_count = len(self.active_groups)
                    total_intercepted = 0
                    total_ghosts = 0
                    operator_count = 0
                    if self.db:
                        try:
                            async with self.db.lock:
                                self.db.cursor.execute("SELECT COUNT(*) FROM intercepted_messages")
                                total_intercepted = self.db.cursor.fetchone()[0]
                                self.db.cursor.execute("SELECT COUNT(*) FROM intercepted_messages WHERE is_deleted = 1")
                                total_ghosts = self.db.cursor.fetchone()[0]
                                self.db.cursor.execute("SELECT COUNT(*) FROM operator_clearances WHERE status = 'active'")
                                operator_count = self.db.cursor.fetchone()[0]
                        except Exception as dbe:
                            logger.warning(f"Status DB query note: {dbe}")
                    
                    status_msg = (
                        "⚡ <b>SUPERBAN OS // TACTICAL STATUS REPORT</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👑 <b>Master Owner:</b> @archfiend000 [Clearance Active]\n"
                        f"🛡️ <b>Monitored Groups:</b> <code>{active_grp_count}</code> groups active\n"
                        f"📡 <b>Wiretapped Interceptions:</b> <code>{total_intercepted}</code> messages\n"
                        f"👻 <b>Ghost Vault Recoveries:</b> <code>{total_ghosts}</code> deleted items\n"
                        f"🎖️ <b>Active Clearances:</b> <code>{operator_count}</code> authorized operators\n"
                        f"⏱️ <b>System Uptime:</b> Operational & Healthy\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "✅ <i>All 5 Non-Admin Superpowers Active & Defending.</i>"
                    )
                    await reply_safe(status_msg)

                elif cmd == '/assign':
                    if len(parts) < 3:
                        await reply_safe(
                            "⚠️ <b>Usage:</b> <code>/assign &lt;@username&gt; &lt;4-digit-code&gt;</code>\n"
                            "<i>Example: /assign @shadow_agent 4812</i>"
                        )
                        return
                    target_user = parts[1].strip().lstrip('@').lower()
                    target_pin = parts[2].strip()
                    if not target_pin.isdigit() or len(target_pin) != 4:
                        await reply_safe("❌ Error: Assigned code must be exactly 4 digits (e.g. <code>4812</code>).")
                        return
                    
                    pin_hash = Config.hash_pin(target_pin)
                    if self.db:
                        await self.db.save_operator_clearance(target_user, pin_hash, role='operator', assigned_by='archfiend000')
                    if hasattr(self, 'engine') and self.engine:
                        await self.engine.notify_owner_clearance_issued(target_user, target_pin, role='operator')
                        try:
                            await self.engine.send_telegram_dm(
                                target_user,
                                f"🛡️ <b>SUPERBAN OS CLEARANCE GRANTED</b>\n"
                                f"Master Owner @archfiend000 has authorized your username @{target_user} with 4-digit code: <code>{target_pin}</code>."
                            )
                        except Exception:
                            pass
                    
                    await reply_safe(
                        f"✅ <b>CLEARANCE GRANTED BY MASTER OWNER</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>Operator:</b> @{target_user}\n"
                        f"🔑 <b>Assigned PIN:</b> <code>{target_pin}</code>\n"
                        f"🎖️ <b>Role:</b> OPERATOR (Active)\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"<i>Credentials dispatched to operator and backed up in Saved Messages.</i>"
                    )

                elif cmd == '/revoke':
                    if len(parts) < 2:
                        await reply_safe("⚠️ <b>Usage:</b> <code>/revoke &lt;@username&gt;</code>")
                        return
                    target_user = parts[1].strip().lstrip('@').lower()
                    if target_user == 'archfiend000':
                        await reply_safe("❌ Cannot revoke Master Owner @archfiend000.")
                        return
                    
                    revoked = False
                    if self.db:
                        revoked = await self.db.revoke_operator_clearance(target_user)
                    if hasattr(self, 'engine') and self.engine:
                        await self.engine.notify_owner_clearance_revoked(target_user)
                    
                    try:
                        from web.app import OPERATOR_SESSIONS
                        tokens_to_kill = [t for t, d in OPERATOR_SESSIONS.items() if d.get('username') == target_user]
                        for t in tokens_to_kill:
                            OPERATOR_SESSIONS.pop(t, None)
                    except Exception:
                        pass

                    if revoked:
                        await reply_safe(f"🚫 <b>REVOKED:</b> Operator @{target_user} clearance terminated and active sessions killed.")
                    else:
                        await reply_safe(f"⚠️ Operator @{target_user} not found or already revoked.")

                elif cmd == '/operators':
                    ops = []
                    if self.db:
                        ops = await self.db.list_operator_clearances()
                    if not ops:
                        await reply_safe("ℹ️ No operators registered.")
                        return
                    
                    lines_out = ["🎖️ <b>SUPERBAN OS // AUTHORIZED OPERATORS</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
                    for o in ops:
                        status_emoji = "🟢" if o.get('status') == 'active' else "🔴"
                        role_badge = "👑 OWNER" if o.get('role') == 'owner' else "🛡️ OPERATOR"
                        lines_out.append(f"{status_emoji} <b>@{o.get('username')}</b> — {role_badge} [{o.get('status').upper()}]")
                    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    await reply_safe("\n".join(lines_out))

                elif cmd == '/radar':
                    recent_entities = []
                    if self.db:
                        try:
                            async with self.db.lock:
                                self.db.cursor.execute(
                                    "SELECT entity_type, entity_value, group_title FROM extracted_entities ORDER BY id DESC LIMIT 6"
                                )
                                recent_entities = self.db.cursor.fetchall()
                        except Exception:
                            pass
                    
                    lines_out = ["📡 <b>SUPERBAN OS // ALPHA & THREAT RADAR</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
                    if recent_entities:
                        for r in recent_entities:
                            lines_out.append(f"• <code>[{r[0]}]</code> <b>{r[1]}</b> ({r[2] or 'Global'})")
                    else:
                        lines_out.append("<i>No high-frequency alpha markers recorded in recent window.</i>")
                    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    await reply_safe("\n".join(lines_out))

                elif cmd == '/ghost':
                    ghosts = []
                    if self.db:
                        ghosts = await self.db.get_ghost_messages(filter_mode='deleted', limit=3)
                    
                    lines_out = [
                        "👻 <b>SUPERBAN OS // GHOST VAULT INTERCEPTIONS</b>",
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    ]
                    if ghosts:
                        for g in ghosts:
                            lines_out.append(f"• <b>{g.get('full_name')}</b> (@{g.get('username') or 'anon'}) in <i>{g.get('group_title')}</i>:")
                            text_preview = (g.get('text') or '[Media/Empty]')[:80]
                            lines_out.append(f"  <blockquote>{text_preview}</blockquote>")
                    else:
                        lines_out.append("<i>Zero deleted messages recovered yet. Standing by.</i>")
                    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
                    await reply_safe("\n".join(lines_out))

                elif cmd == '/vc':
                    vc_info = getattr(self.engine, 'active_vc_session', None) if hasattr(self, 'engine') and self.engine else None
                    if vc_info:
                        msg = (
                            "🎙️ <b>VOICE CHAMBER APEX ACTIVE</b>\n"
                            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👥 <b>Group ID:</b> <code>{vc_info.get('group_id')}</code>\n"
                            f"🔊 <b>Status:</b> Listening & WebRTC Streaming\n"
                            f"🎤 <b>Live Audio Buffer:</b> /tmp/vc_stream_{vc_info.get('clean_group_id', 'live')}.mp3"
                        )
                    else:
                        msg = "🎙️ <b>VOICE CHAMBER:</b> No active voice call currently joined. Standing by."
                    await reply_safe(msg)

                elif cmd == '/report':
                    if len(parts) < 3:
                        await reply_safe("⚠️ <b>Usage:</b> <code>/report &lt;group_id&gt; &lt;msg_id&gt; [reason]</code>")
                        return
                    try:
                        rep_gid = int(parts[1])
                        rep_mid = int(parts[2])
                        rep_reason = parts[3] if len(parts) > 3 else "spam"
                        if hasattr(self, 'engine') and self.engine:
                            res = await self.engine.report_threat_to_telegram(rep_gid, rep_mid, reason=rep_reason)
                            if res.get("success"):
                                await reply_safe(f"🚨 <b>MTProto Report Dispatched:</b> Msg #{rep_mid} in {rep_gid} reported to Telegram Trust & Safety as <code>{rep_reason}</code>.")
                            else:
                                await reply_safe(f"❌ Failed to report: {res.get('error')}")
                    except Exception as ex:
                        await reply_safe(f"❌ Syntax Error: {ex}")

            except Exception as e:
                logger.error(f"Companion controller error: {e}", exc_info=True)
                try:
                    await reply_safe(f"⚠️ <b>Execution Error:</b> {html.escape(str(e))}")
                except Exception:
                    pass

        self._handlers_registered = True

    async def _check_surveillance(self, group_id: int, group_title: str, user_id: int, 
                                  full_name: str, username: str, text: str):
        """Cross-examine incoming events against active surveillance watchlists."""
        try:
            watchlist = await self.db.get_watchlist()
            if not watchlist:
                return

            text_lower = (text or "").lower()
            u_lower = (username or "").lower()
            uid_str = str(user_id)

            for rule in watchlist:
                target_type = rule.get("target_type")
                val = str(rule.get("value", "")).lower()
                if not val:
                    continue

                matched = False
                if target_type == "keyword" and val in text_lower:
                    matched = True
                elif target_type == "user" and (val == uid_str or val == u_lower or val.lstrip('@') == u_lower.lstrip('@')):
                    matched = True

                if matched:
                    logger.warning(f"🚨 SURVEILLANCE HIT [{target_type}:{val}] triggered by {full_name} in {group_title}!")
                    await self.db.log_surveillance_alert(
                        watchlist_id=rule.get("id"),
                        target_type=target_type,
                        trigger_val=val,
                        group_id=group_id,
                        group_title=group_title,
                        user_id=user_id,
                        full_name=full_name,
                        username=username,
                        message_text=text,
                        action_context="message"
                    )
                    await self.emit_sentinel_event("surveillance_target_hit", {
                        "target_type": target_type,
                        "trigger_val": val,
                        "group_id": group_id,
                        "group_title": group_title,
                        "user_id": user_id,
                        "full_name": full_name,
                        "username": username,
                        "message_text": text,
                        "timestamp": time.strftime("%H:%M:%S", time.localtime())
                    })
        except Exception as e:
            logger.debug(f"Surveillance check note: {e}")

    def _extract_entities(self, text: str, group_id: int, group_title: str, 
                          message_id: int, sender_id: int, sender_name: str) -> List[Dict[str, Any]]:
        """Extract cryptocurrency addresses, Telegram invites, URLs, and cashtags."""
        if not text:
            return []
        entities = []
        seen = set()

        # EVM / Ethereum
        for eth in ETH_REGEX.findall(text):
            if eth.lower() not in seen:
                seen.add(eth.lower())
                entities.append({
                    "group_id": group_id, "group_title": group_title, "message_id": message_id,
                    "sender_id": sender_id, "sender_name": sender_name,
                    "entity_type": "crypto_eth", "entity_value": eth, "context": text
                })

        # Solana
        for sol in SOL_REGEX.findall(text):
            if sol.lower() not in seen and len(sol) >= 32 and not sol.startswith('http'):
                if not sol.startswith('0x') and any(c.isdigit() for c in sol) and any(c.isalpha() for c in sol):
                    seen.add(sol.lower())
                    entities.append({
                        "group_id": group_id, "group_title": group_title, "message_id": message_id,
                        "sender_id": sender_id, "sender_name": sender_name,
                        "entity_type": "crypto_sol", "entity_value": sol, "context": text
                    })

        # Bitcoin
        for btc in BTC_REGEX.findall(text):
            if btc.lower() not in seen:
                seen.add(btc.lower())
                entities.append({
                    "group_id": group_id, "group_title": group_title, "message_id": message_id,
                    "sender_id": sender_id, "sender_name": sender_name,
                    "entity_type": "crypto_btc", "entity_value": btc, "context": text
                })

        # TON
        for ton in TON_REGEX.findall(text):
            if ton.lower() not in seen:
                seen.add(ton.lower())
                entities.append({
                    "group_id": group_id, "group_title": group_title, "message_id": message_id,
                    "sender_id": sender_id, "sender_name": sender_name,
                    "entity_type": "crypto_ton", "entity_value": ton, "context": text
                })

        # Telegram Invites
        for inv in TG_INVITE_REGEX.findall(text):
            if inv.lower() not in seen:
                seen.add(inv.lower())
                entities.append({
                    "group_id": group_id, "group_title": group_title, "message_id": message_id,
                    "sender_id": sender_id, "sender_name": sender_name,
                    "entity_type": "tg_invite", "entity_value": inv, "context": text
                })

        # Cashtags ($SOL, $BTC)
        for tag in CASHTAG_REGEX.findall(text):
            tag_clean = tag.upper()
            if tag_clean not in seen:
                seen.add(tag_clean)
                entities.append({
                    "group_id": group_id, "group_title": group_title, "message_id": message_id,
                    "sender_id": sender_id, "sender_name": sender_name,
                    "entity_type": "cashtag", "entity_value": f"${tag_clean}", "context": text
                })

        # URLs
        for url in URL_REGEX.findall(text):
            if url.lower() not in seen and not any(url in e["entity_value"] for e in entities):
                seen.add(url.lower())
                entities.append({
                    "group_id": group_id, "group_title": group_title, "message_id": message_id,
                    "sender_id": sender_id, "sender_name": sender_name,
                    "entity_type": "url", "entity_value": url, "context": text
                })

        # Emails
        for em in EMAIL_REGEX.findall(text):
            if em.lower() not in seen:
                seen.add(em.lower())
                entities.append({
                    "group_id": group_id, "group_title": group_title, "message_id": message_id,
                    "sender_id": sender_id, "sender_name": sender_name,
                    "entity_type": "email", "entity_value": em, "context": text
                })

        return entities

    async def _track_interaction(self, event, group_id: int, source_user_id: int, source_name: str, text: str):
        """Map directional social communication and mentions."""
        try:
            # 1. Replies
            if event.is_reply:
                reply_msg = await event.get_reply_message()
                if reply_msg and reply_msg.sender_id and reply_msg.sender_id != source_user_id:
                    reply_sender = await reply_msg.get_sender()
                    target_name = ""
                    if reply_sender:
                        fn = getattr(reply_sender, 'first_name', '') or ''
                        ln = getattr(reply_sender, 'last_name', '') or ''
                        target_name = f"{fn} {ln}".strip() or getattr(reply_sender, 'username', '') or f"User #{reply_msg.sender_id}"
                    await self.db.record_social_interaction(
                        group_id, source_user_id, source_name, reply_msg.sender_id, target_name, "reply", event.id
                    )

            # 2. @mentions
            mentions = re.findall(r'@([a-zA-Z0-9_]{4,32})', text or "")
            for m in set(mentions):
                await self.db.record_social_interaction(
                    group_id, source_user_id, source_name, 0, f"@{m}", "mention", event.id
                )
        except Exception as e:
            logger.debug(f"Social interaction track note: {e}")

    async def _check_siphon(self, group_id: int, group_title: str, user_id: int, 
                            full_name: str, username: str, text: str, message_id: int, 
                            entities: List[Dict[str, Any]]):
        """Autonomous Siphon Matrix: delivers high-priority intelligence straight to Telegram Saved Messages."""
        try:
            rules = await self.db.get_siphon_rules()
            if not rules:
                return

            text_lower = (text or "").lower()
            uid_str = str(user_id)
            u_lower = (username or "").lower()

            for rule in rules:
                if not rule.get("is_active"):
                    continue

                m_type = rule.get("match_type")
                m_val = str(rule.get("match_value", "")).lower()
                dest = rule.get("destination", "me")
                rule_name = rule.get("rule_name", "Siphon Trigger")
                rule_id = rule.get("id")

                matched = False
                matched_snippet = text

                if m_type == "keyword" and m_val in text_lower:
                    matched = True
                elif m_type == "user" and (m_val == uid_str or m_val == u_lower or m_val.lstrip('@') == u_lower.lstrip('@')):
                    matched = True
                elif m_type == "crypto":
                    crypto_entities = [e for e in entities if e["entity_type"].startswith("crypto_")]
                    if crypto_entities:
                        matched = True
                        matched_snippet = f"[{crypto_entities[0]['entity_type'].upper()}] {crypto_entities[0]['entity_value']}\n{text}"

                if matched:
                    logger.warning(f"🛸 SIPHON TRIGGERED [{rule_name}] -> Forwarding to {dest}!")
                    await self.db.log_siphon_event(
                        rule_id, rule_name, group_id, group_title, full_name, matched_snippet, dest
                    )
                    await self.emit_sentinel_event("siphon_triggered", {
                        "rule_id": rule_id,
                        "rule_name": rule_name,
                        "group_title": group_title,
                        "sender_name": full_name,
                        "matched_text": matched_snippet,
                        "destination": dest,
                        "timestamp": time.strftime("%H:%M:%S", time.localtime())
                    })

                    memo = (
                        f"🛸 <b>[SUPERBAN OS // AUTONOMOUS SIPHON]</b>\n"
                        f"🎯 <b>Rule:</b> {rule_name} (<code>{m_type}</code>)\n"
                        f"👥 <b>Group:</b> {group_title} (<code>{group_id}</code>)\n"
                        f"👤 <b>Target:</b> {full_name} (@{username or 'no_user'} | ID: <code>{user_id}</code>)\n"
                        f"───────────────────\n"
                        f"💬 <b>Intercepted:</b>\n"
                        f"{matched_snippet}\n"
                        f"───────────────────\n"
                        f"⏱ <i>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}</i>"
                    )
                    try:
                        await self.client.send_message(dest, memo, parse_mode="html")
                    except Exception as err:
                        logger.error(f"Failed to deliver siphon memo to {dest}: {err}")
        except Exception as e:
            logger.debug(f"Siphon check note: {e}")

    async def _check_deleted_siphon(self, rec: Dict[str, Any]):
        """Siphon deleted messages to Telegram Saved Messages."""
        try:
            rules = await self.db.get_siphon_rules()
            deleted_rules = [r for r in rules if r.get("is_active") and r.get("match_type") == "all_deleted"]
            if not deleted_rules:
                return

            dest = deleted_rules[0].get("destination", "me")
            rule_id = deleted_rules[0].get("id")
            rule_name = deleted_rules[0].get("rule_name", "Deleted Message Wiretap")

            memo = (
                f"🚨 <b>[GHOST VAULT // DELETED MESSAGE SIPHON]</b>\n"
                f"👥 <b>Group:</b> {rec.get('group_title')} (<code>{rec.get('group_id')}</code>)\n"
                f"👤 <b>Sender:</b> {rec.get('full_name')} (@{rec.get('username') or 'no_user'} | ID: <code>{rec.get('user_id')}</code>)\n"
                f"───────────────────\n"
                f"🚨 <b>DELETED TEXT PRESERVED:</b>\n"
                f"{rec.get('original_text') or rec.get('message_text') or '(empty)'}\n"
                f"───────────────────\n"
                f"⏱ <i>Deleted at: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}</i>"
            )
            await self.db.log_siphon_event(
                rule_id, rule_name, rec.get("group_id", 0), rec.get("group_title", ""),
                rec.get("full_name", ""), rec.get("original_text") or rec.get("message_text") or "", dest
            )
            await self.client.send_message(dest, memo, parse_mode="html")
        except Exception as e:
            logger.debug(f"Deleted siphon note: {e}")

    async def set_defcon_level(self, group_id: int, level: int, slowmode_seconds: int = 0) -> int:
        """Apply DEFCON defense protocol to group."""
        if group_id not in self.active_groups:
            self.enable_shield(group_id)
        
        self.active_groups[group_id]["defcon"] = level
        await self.db.set_defcon(group_id, level, slowmode_seconds)

        if level == 1:
            await self.lockdown_group(group_id)
            self.active_groups[group_id]["raid_threshold"] = 2
        elif level == 2:
            self.active_groups[group_id]["raid_threshold"] = 3
            self.active_groups[group_id]["auto_ban_scammers"] = True
        elif level == 5:
            await self.unlock_group(group_id)
            self.active_groups[group_id]["raid_threshold"] = 5

        logger.critical(f"🛡️ DEFCON {level} engaged for group {group_id}")
        await self.emit_sentinel_event("defcon_changed", {"group_id": group_id, "level": level})
        return level

    async def get_defcon_level(self, group_id: int) -> Dict[str, Any]:
        return await self.db.get_defcon(group_id)

    def enable_shield(self, group_id: int, options: Optional[Dict[str, Any]] = None):
        default_opts = {
            "auto_ban_scammers": True,
            "auto_lockdown_on_raid": True,
            "anti_spam_messages": True,
            "ban_message_spammers": True,
            "raid_threshold": 5
        }
        if options:
            default_opts.update(options)
        self.active_groups[group_id] = default_opts
        self.stats["active_shields"] = len(self.active_groups)
        logger.info(f"🛡️ Sentinel Shield ENABLED for group {group_id}")

    def disable_shield(self, group_id: int):
        if group_id in self.active_groups:
            del self.active_groups[group_id]
            self.stats["active_shields"] = len(self.active_groups)
            logger.info(f"Sentinel Shield disabled for group {group_id}")

    def is_shield_active(self, group_id: int) -> bool:
        return group_id in self.active_groups

    async def lockdown_group(self, group_id: int) -> bool:
        """Lock down chat: mute all standard members immediately."""
        try:
            entity = await self.client.get_entity(group_id)
            rights = ChatBannedRights(
                until_date=None,
                send_messages=True,
                send_media=True,
                send_stickers=True,
                send_gifs=True,
                send_games=True,
                send_inline=True,
                embed_links=True
            )
            await self.client(EditChatDefaultBannedRightsRequest(peer=entity, banned_rights=rights))
            logger.warning(f"🔒 Group {group_id} has entered TOTAL LOCKDOWN.")
            await self.emit_sentinel_event("lockdown_engaged", {"group_id": group_id})
            return True
        except Exception as e:
            logger.error(f"Failed to lockdown group {group_id}: {e}")
            return False

    async def unlock_group(self, group_id: int) -> bool:
        """Unlock chat: restore standard messaging privileges."""
        try:
            entity = await self.client.get_entity(group_id)
            rights = ChatBannedRights(
                until_date=None,
                send_messages=False,
                send_media=False,
                send_stickers=False,
                send_gifs=False,
                send_games=False,
                send_inline=False,
                embed_links=False
            )
            await self.client(EditChatDefaultBannedRightsRequest(peer=entity, banned_rights=rights))
            logger.info(f"🔓 Group {group_id} has been UNLOCKED.")
            await self.emit_sentinel_event("lockdown_released", {"group_id": group_id})
            return True
        except Exception as e:
            logger.error(f"Failed to unlock group {group_id}: {e}")
            return False

    async def analyze_chat_activity(self, group_entity, limit: int = 1500) -> Dict[str, Any]:
        """Deep scan chat message history to extract top chatters and community activity."""
        user_counts = defaultdict(int)
        user_last_msg = {}
        total_msgs = 0

        async for msg in self.client.iter_messages(group_entity, limit=limit):
            total_msgs += 1
            if msg.from_id:
                sender_id = getattr(msg.from_id, 'user_id', None)
                if sender_id:
                    user_counts[sender_id] += 1
                    if sender_id not in user_last_msg:
                        user_last_msg[sender_id] = msg.date

        # Resolve top chatters
        sorted_chatters = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:30]
        leaderboard = []

        for uid, count in sorted_chatters:
            try:
                u = await self.client.get_entity(uid)
                leaderboard.append({
                    "user_id": uid,
                    "name": f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip() or "Anonymous",
                    "username": getattr(u, 'username', '') or '',
                    "message_count": count,
                    "share_pct": round((count / total_msgs * 100), 1) if total_msgs else 0
                })
            except Exception:
                leaderboard.append({
                    "user_id": uid, "name": f"User {uid}", "username": "",
                    "message_count": count, "share_pct": round((count / total_msgs * 100), 1) if total_msgs else 0
                })

        return {
            "total_analyzed": total_msgs,
            "unique_chatters": len(user_counts),
            "leaderboard": leaderboard
        }
