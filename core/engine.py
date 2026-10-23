import asyncio
import time
import logging
import uuid
import random
import json
import re
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Callable, Set
from telethon import TelegramClient, events, types
from telethon.tl.functions.channels import EditBannedRequest, GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions import phone
from telethon.tl.types import (
    ChatBannedRights, ChannelParticipantsAdmins,
    InputGroupCall, GroupCall, GroupCallDiscarded,
    DataJSON, InputPeerSelf, UpdateGroupCallConnection
)
from telethon.errors import (
    FloodWaitError, ChatAdminRequiredError, 
    UserNotParticipantError, UserPrivacyRestrictedError, RPCError
)

import os
from pathlib import Path

# Ensure virtual environment bin (with ffmpeg) is always prioritized in PATH
_venv_bin = str(Path(__file__).resolve().parent.parent / "venv" / "bin")
if _venv_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{_venv_bin}:{os.environ.get('PATH', '')}"

try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream, RecordStream
    HAS_PYTGCALLS = True
except ImportError:
    HAS_PYTGCALLS = False

from core.db import DatabaseManager
from core.rate_limiter import AdaptiveRateLimiter
from core.forensics import ForensicInspector
from core.sentinel import SentinelShield

logger = logging.getLogger("SuperBanEngine")

class OperationState:
    IDLE = "IDLE"
    SCANNING = "SCANNING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ABORTED = "ABORTED"
    COMPLETED = "COMPLETED"

class SuperBanEngine:
    """Mission-Critical Telegram Administrative Engine with Live Control & Multi-Actions."""

    MODES = {
        'stealth':    {'concurrent': 1,  'delay': 3.0, 'desc': 'Ultra stealth, lowest risk'},
        'normal':     {'concurrent': 3,  'delay': 1.5, 'desc': 'Balanced, recommended for groups'},
        'fast':       {'concurrent': 7,  'delay': 0.7, 'desc': 'Rapid moderation actions'},
        'turbo':      {'concurrent': 12, 'delay': 0.35,'desc': 'High throughput parallel pool'},
        'nuclear':    {'concurrent': 20, 'delay': 0.15,'desc': 'High aggression limit tester'}
    }

    def __init__(self, client: TelegramClient, db: DatabaseManager, extra_clients: Optional[List[TelegramClient]] = None):
        self.client = client
        self.clients = [client] + (extra_clients or [])
        self.db = db
        self.rate_limiter = AdaptiveRateLimiter()
        
        self.state = OperationState.IDLE
        self.current_session_id: Optional[str] = None
        self.current_mode = 'normal'
        
        # Rights templates
        self.rights_ban = ChatBannedRights(until_date=None, view_messages=True, send_messages=True)
        self.rights_mute = ChatBannedRights(until_date=None, send_messages=True, send_media=True, send_stickers=True)
        self.rights_unban = ChatBannedRights(until_date=None)
        
        # Live Telemetry
        self.telemetry = {
            "total": 0, "processed": 0, "success": 0, "failed": 0,
            "speed": 0.0, "eta_seconds": 0, "floods": 0, "state": self.state,
            "action": "ban", "group_name": "", "start_time": 0.0
        }
        
        self.event_listeners: List[Callable[[Dict[str, Any]], Any]] = []
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        
        # Real-Time Autonomous Sentinel
        self.sentinel = SentinelShield(self.client, self.db)
        self.sentinel.register_callback(lambda evt: asyncio.create_task(self.emit_telemetry(evt["type"], evt.get("data", {}))))

        # Active Voice Chat Tracking Session
        self.active_vc_session: Optional[Dict[str, Any]] = None

        # Native PyTgCalls WebRTC Engine
        self.call_py: Optional[Any] = None
        self._call_py_started = False

        # Real-Time MTProto Voice Chat WebRTC Transport Listener
        @self.client.on(events.Raw(types.UpdateGroupCallConnection))
        async def _on_vc_connection_update(event):
            try:
                data = json.loads(event.params.data)
                logger.info(f"🎙️ MTProto UpdateGroupCallConnection received (candidates: {len(data.get('transport', {}).get('candidates', []))})")
                if self.active_vc_session:
                    self.active_vc_session['server_connection'] = data
            except Exception as e:
                logger.debug(f"Failed to parse UpdateGroupCallConnection: {e}")

    def register_listener(self, callback: Callable[[Dict[str, Any]], Any]):
        """Subscribe to real-time telemetry updates."""
        self.event_listeners.append(callback)

    async def emit_telemetry(self, event_type: str = "progress", extra: Optional[Dict[str, Any]] = None):
        payload = {"type": event_type, "telemetry": self.telemetry, "timestamp": time.time()}
        if extra:
            payload.update(extra)
        for listener in self.event_listeners:
            try:
                res = listener(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.debug(f"Telemetry listener error: {e}")

    async def ensure_connected(self):
        """Ensure client is actively connected to Telegram MTProto network."""
        if not self.client.is_connected():
            try:
                await self.client.connect()
            except Exception as e:
                logger.warning(f"Engine client auto-reconnect attempt: {e}")

    async def get_dialogs(self, force: bool = False):
        """Fetch accessible groups and supergroups with 60s TTL cache."""
        if not force and hasattr(self, '_dialogs_cache') and self._dialogs_cache:
            if time.time() - getattr(self, '_dialogs_cache_time', 0) < 60:
                return self._dialogs_cache

        await self.ensure_connected()
        dialogs = await self.client.get_dialogs()
        groups = []
        for d in dialogs:
            if d.is_group or d.is_channel:
                groups.append({
                    "id": d.id,
                    "title": d.name,
                    "entity": d.entity,
                    "is_channel": d.is_channel,
                    "is_group": d.is_group,
                    "members_count": getattr(d.entity, 'participants_count', 0)
                })
        self._dialogs_cache = groups
        self._dialogs_cache_time = time.time()
        return groups

    async def get_group_entity(self, group_id: int):
        """Fast entity resolution without triggering MTProto flood wait."""
        await self.ensure_connected()
        clean_target = str(group_id).replace("-100", "").replace("-", "")
        if hasattr(self, '_dialogs_cache') and self._dialogs_cache:
            for g in self._dialogs_cache:
                clean_g = str(g["id"]).replace("-100", "").replace("-", "")
                if clean_g == clean_target:
                    return g["entity"]
        try:
            return await self.client.get_entity(group_id)
        except Exception:
            pass
        dialogs = await self.get_dialogs(force=True)
        for g in dialogs:
            clean_g = str(g["id"]).replace("-100", "").replace("-", "")
            if clean_g == clean_target:
                return g["entity"]
        return None

    async def get_admins(self, group_entity) -> Set[int]:
        """Fetch protected group administrators with 5-minute caching."""
        gid = getattr(group_entity, 'id', str(group_entity))
        if not hasattr(self, '_admins_cache'):
            self._admins_cache = {}
        cached = self._admins_cache.get(gid)
        if cached and (time.time() - cached['time'] < 300):
            return cached['admins']

        admins = set()
        try:
            async for admin in self.client.iter_participants(group_entity, filter=ChannelParticipantsAdmins):
                admins.add(admin.id)
            self._admins_cache[gid] = {'admins': admins, 'time': time.time()}
        except Exception as e:
            logger.warning(f"Admin discovery warning: {e}")
            if cached:
                return cached['admins']
        return admins

    async def get_recent_chat_messages(self, group_entity, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch recent message history for AI synthesis and intelligence forensics."""
        messages = []
        try:
            async for msg in self.client.iter_messages(group_entity, limit=limit):
                if not msg.raw_text:
                    continue
                sender = msg.sender
                full_name = "User"
                username = ""
                user_id = 0
                trust_score = 70
                trust_level = "NEUTRAL CITIZEN"
                if sender:
                    user_id = getattr(sender, 'id', 0)
                    fn = getattr(sender, 'first_name', '') or ''
                    ln = getattr(sender, 'last_name', '') or ''
                    full_name = f"{fn} {ln}".strip() or "User"
                    username = getattr(sender, 'username', '') or ''
                    profile = ForensicInspector.inspect_user(sender)
                    trust_score = profile.get("trust_score", 70)
                    trust_level = profile.get("trust_level", "NEUTRAL CITIZEN")

                messages.append({
                    "message_id": msg.id,
                    "user_id": user_id,
                    "full_name": full_name,
                    "username": username,
                    "text": msg.raw_text,
                    "date": msg.date.strftime("%H:%M:%S") if msg.date else "",
                    "trust_score": trust_score,
                    "trust_level": trust_level
                })
        except Exception as e:
            logger.error(f"Error fetching recent chat messages: {e}")
        return messages

    async def inspect_group_members(self, group_entity, limit: int = 5000) -> List[Dict[str, Any]]:
        """Deep scan and profile group participants with forensic intelligence."""
        self.state = OperationState.SCANNING
        await self.emit_telemetry("status_change", {"message": "Scanning group participants..."})
        
        inspected = []
        try:
            async for user in self.client.iter_participants(group_entity, limit=limit):
                profile = ForensicInspector.inspect_user(user)
                inspected.append(profile)
                if len(inspected) % 200 == 0:
                    await self.emit_telemetry("scan_progress", {"scanned": len(inspected)})
        except ChatAdminRequiredError:
            logger.info("Direct participant list is hidden by group admin. Harvesting active members from chat messages...")
            await self.emit_telemetry("status_change", {"message": "Member list hidden. Harvesting active chatters..."})
            seen_ids = set()
            async for msg in self.client.iter_messages(group_entity, limit=min(limit * 2, 4000)):
                sender = msg.sender
                if sender and hasattr(sender, 'id') and sender.id not in seen_ids:
                    seen_ids.add(sender.id)
                    profile = ForensicInspector.inspect_user(sender)
                    inspected.append(profile)
                    if len(inspected) % 100 == 0:
                        await self.emit_telemetry("scan_progress", {"scanned": len(inspected)})
                
        self.state = OperationState.IDLE
        await self.emit_telemetry("scan_complete", {"total_scanned": len(inspected)})
        return inspected

    def build_banned_rights(self, action: str, duration_seconds: int = 0, permission_subtype: str = "full") -> ChatBannedRights:
        until_date = None
        if duration_seconds > 0:
            until_date = datetime.now() + timedelta(seconds=duration_seconds)

        if action == "ban":
            return ChatBannedRights(until_date=until_date, view_messages=True, send_messages=True)
        elif action == "mute":
            if permission_subtype == "mute_links":
                return ChatBannedRights(until_date=until_date, embed_links=True)
            elif permission_subtype == "mute_media":
                return ChatBannedRights(
                    until_date=until_date, send_media=True, send_stickers=True,
                    send_gifs=True, send_games=True, send_inline=True
                )
            else: # mute_all
                return ChatBannedRights(
                    until_date=until_date, send_messages=True, send_media=True,
                    send_stickers=True, send_gifs=True, send_games=True,
                    send_inline=True, embed_links=True
                )
        elif action == "unban":
            return ChatBannedRights(until_date=None)
        return self.rights_ban

    async def clean_service_messages(self, group_entity, limit: int = 2500) -> int:
        """Purge all cluttering service events ('user joined', 'user left', 'pinned message', 'photo updated')."""
        batch = []
        purged = 0
        async for msg in self.client.iter_messages(group_entity, limit=limit):
            if msg.action is not None:
                batch.append(msg.id)
                if len(batch) >= 100:
                    try:
                        await self.client.delete_messages(group_entity, batch)
                        purged += len(batch)
                        batch.clear()
                        await asyncio.sleep(0.6)
                    except Exception as e:
                        logger.debug(f"Batch purge error: {e}")
                        batch.clear()
        if batch:
            try:
                await self.client.delete_messages(group_entity, batch)
                purged += len(batch)
            except Exception:
                pass
        return purged

    async def scrape_leads(self, group_entity, active_only: bool = True, limit: int = 3000) -> List[Dict[str, Any]]:
        """Scrape active, real user leads from any group and save to DB."""
        leads = []
        group_id = group_entity.id
        group_title = getattr(group_entity, 'title', 'Group')
        
        try:
            async for user in self.client.iter_participants(group_entity, limit=limit):
                profile = ForensicInspector.inspect_user(user)
                if active_only:
                    if profile["is_bot"] or profile["is_deleted"]:
                        continue
                    if profile["days_offline"] is not None and profile["days_offline"] > 7:
                        continue
                leads.append(profile)
        except ChatAdminRequiredError:
            logger.info(f"Group {group_id} has hidden member list. Harvesting active leads from recent messages...")
            seen_ids = set()
            async for msg in self.client.iter_messages(group_entity, limit=min(limit * 2, 4000)):
                sender = msg.sender
                if sender and hasattr(sender, 'id') and sender.id not in seen_ids:
                    seen_ids.add(sender.id)
                    profile = ForensicInspector.inspect_user(sender)
                    if active_only:
                        if profile["is_bot"] or profile["is_deleted"]:
                            continue
                        if profile["days_offline"] is not None and profile["days_offline"] > 7:
                            continue
                    leads.append(profile)

        await self.db.save_scraped_leads(leads, group_id, group_title)
        return leads

    async def execute_action_on_user_id(self, group_entity, user_id: int, action: str, purge_messages: bool = False, duration_seconds: int = 0) -> bool:
        """Execute action on a single user ID immediately."""
        target = {"id": user_id, "username": "", "full_name": ""}
        group_id = group_entity.id
        group_name = getattr(group_entity, 'title', 'Group')
        return await self.execute_action_single(self.client, group_entity, target, action, group_id, group_name, purge_messages)

    async def execute_action_single(self, client: TelegramClient, group_entity, target: Dict[str, Any], 
                                    action: str, group_id: int, group_name: str, 
                                    purge_messages: bool = False, duration_seconds: int = 0,
                                    permission_subtype: str = "full", max_retries: int = 3) -> bool:
        """Execute single targeted action with granular rights & durations."""
        user_id = target["id"]
        username = target.get("username", "")
        first_name = target.get("full_name", "")
        rights = self.build_banned_rights(action, duration_seconds, permission_subtype)

        for attempt in range(max_retries):
            await self._pause_event.wait()
            if self.state == OperationState.ABORTED:
                return False

            try:
                if action == "kick":
                    await client(EditBannedRequest(channel=group_entity, participant=user_id, banned_rights=self.rights_ban))
                    await client(EditBannedRequest(channel=group_entity, participant=user_id, banned_rights=self.rights_unban))
                else:
                    await client(EditBannedRequest(channel=group_entity, participant=user_id, banned_rights=rights))

                if purge_messages:
                    try:
                        msg_ids = [m.id async for m in client.iter_messages(group_entity, from_user=user_id, limit=50)]
                        if msg_ids:
                            await client.delete_messages(group_entity, msg_ids)
                    except Exception:
                        pass

                await self.db.log_action(user_id, username, first_name, group_id, group_name, action, True, duration=duration_seconds)
                self.rate_limiter.on_success()
                return True

            except FloodWaitError as e:
                self.telemetry["floods"] += 1
                self.rate_limiter.on_flood(e.seconds)
                await self.emit_telemetry("flood_wait", {"seconds": e.seconds, "user_id": user_id})
                
                if e.seconds > 180:
                    logger.error(f"🛑 Critical FloodWait ({e.seconds}s). Halting operation to safeguard account.")
                    self.state = OperationState.ABORTED
                    return False
                    
                await asyncio.sleep(e.seconds + 1.5)
                
            except (UserNotParticipantError, UserPrivacyRestrictedError):
                await self.db.log_action(user_id, username, first_name, group_id, group_name, action, False, "Not participant or restricted")
                return False
                
            except ChatAdminRequiredError:
                logger.error("Admin rights lost during batch operation!")
                self.state = OperationState.ABORTED
                return False
                
            except Exception as e:
                logger.debug(f"Action {action} failed for {user_id}: {e}")
                await self.db.log_action(user_id, username, first_name, group_id, group_name, action, False, str(e))
                return False
                
        return False

    async def run_operation(self, group_entity, targets: List[Dict[str, Any]], 
                            action: str = "ban", mode: str = "normal", 
                            purge_messages: bool = False, skip_admins: bool = True,
                            duration_seconds: int = 0, permission_subtype: str = "full"):
        """Launch parallel operational queue with dynamic throttling and real-time eventing."""
        self.state = OperationState.RUNNING
        self._pause_event.set()
        
        mode_cfg = self.MODES.get(mode, self.MODES['normal'])
        self.rate_limiter.current_delay = mode_cfg['delay']
        self.current_mode = mode
        
        group_id = group_entity.id
        group_name = getattr(group_entity, 'title', 'Group')
        self.current_session_id = f"{group_id}_{int(time.time())}_{str(uuid.uuid4())[:6]}"
        
        # Filter protected entities
        admins = await self.get_admins(group_entity) if skip_admins else set()
        whitelist = await self.db.get_whitelist()
        me = await self.client.get_me()
        my_id = me.id if me else None

        filtered_targets = [
            t for t in targets 
            if t['id'] not in admins and t['id'] not in whitelist and t['id'] != my_id
        ]
        
        total = len(filtered_targets)
        self.telemetry.update({
            "total": total, "processed": 0, "success": 0, "failed": 0,
            "speed": 0.0, "eta_seconds": 0, "floods": 0, "state": self.state,
            "action": action, "group_name": group_name, "start_time": time.time()
        })
        
        await self.db.start_session(self.current_session_id, group_id, group_name, total, mode, action)
        await self.emit_telemetry("operation_started", {"session_id": self.current_session_id})
        
        semaphore = asyncio.Semaphore(mode_cfg['concurrent'])
        lock = asyncio.Lock()
        
        async def worker(idx: int, target: Dict[str, Any]):
            if self.state == OperationState.ABORTED:
                return
            async with semaphore:
                client = self.clients[idx % len(self.clients)]
                success = await self.execute_action_single(
                    client, group_entity, target, action, group_id, group_name, 
                    purge_messages=purge_messages, duration_seconds=duration_seconds, 
                    permission_subtype=permission_subtype
                )
                
                async with lock:
                    if success:
                        self.telemetry["success"] += 1
                    else:
                        self.telemetry["failed"] += 1
                    self.telemetry["processed"] += 1
                    
                    # Update speed & ETA
                    elapsed = max(time.time() - self.telemetry["start_time"], 0.1)
                    speed = self.telemetry["processed"] / elapsed
                    remaining = total - self.telemetry["processed"]
                    eta = remaining / speed if speed > 0 else 0
                    
                    self.telemetry["speed"] = round(speed, 2)
                    self.telemetry["eta_seconds"] = int(eta)
                    
                    if self.telemetry["processed"] % 5 == 0 or self.telemetry["processed"] == total:
                        await self.db.update_session_progress(
                            self.current_session_id, 
                            self.telemetry["processed"], 
                            self.telemetry["success"], 
                            self.telemetry["failed"],
                            self.state
                        )
                        await self.emit_telemetry("progress")
                        
                await self.rate_limiter.wait()

        tasks = [worker(i, t) for i, t in enumerate(filtered_targets)]
        await asyncio.gather(*tasks, return_exceptions=True)
        
        if self.state != OperationState.ABORTED:
            self.state = OperationState.COMPLETED
            await self.db.complete_session(self.current_session_id, "completed")
        else:
            await self.db.complete_session(self.current_session_id, "aborted")
            
        self.telemetry["state"] = self.state
        await self.emit_telemetry("operation_finished")

    def pause(self):
        """Pause active operations instantly."""
        if self.state == OperationState.RUNNING:
            self.state = OperationState.PAUSED
            self._pause_event.clear()
            self.telemetry["state"] = self.state
            asyncio.create_task(self.emit_telemetry("paused"))

    def resume(self):
        """Resume paused operation."""
        if self.state == OperationState.PAUSED:
            self.state = OperationState.RUNNING
            self._pause_event.set()
            self.telemetry["state"] = self.state
            asyncio.create_task(self.emit_telemetry("resumed"))

    def abort(self):
        """Instantly abort the running pipeline."""
        self.state = OperationState.ABORTED
        self._pause_event.set()
        self.telemetry["state"] = self.state
        asyncio.create_task(self.emit_telemetry("aborted"))

    async def broadcast_campaign(self, targets: List[Dict[str, Any]], template: str, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """High-Velocity Safe Drip Broadcast Engine with rate limiting and variable interpolation."""
        import uuid
        import random
        cid = campaign_id or f"camp_{uuid.uuid4().hex[:8]}"
        total = len(targets)
        sent = 0
        failed = 0

        await self.db.create_broadcast(cid, f"Broadcast to {total} members", 0, template, total)

        for user in targets:
            uid = user.get("id") or user.get("user_id")
            if not uid:
                failed += 1
                continue

            try:
                # Interpolate variables
                first_name = user.get("full_name") or user.get("first_name") or "there"
                username = user.get("username") or ""
                personalized_msg = template.replace("{first_name}", first_name).replace("{name}", first_name).replace("{username}", username)

                # Send directly to user
                await self.client.send_message(uid, personalized_msg)
                sent += 1
            except Exception as e:
                failed += 1
                logger.debug(f"Broadcast to {uid} failed: {e}")

            # Emit live progress
            if sent % 5 == 0 or sent + failed == total:
                await self.db.update_broadcast_progress(cid, sent, failed, "running" if (sent + failed < total) else "completed")
                await self.emit_telemetry("broadcast_progress")

            # Jitter delay: 1.5s - 3.5s
            await asyncio.sleep(random.uniform(1.2, 2.8))

        await self.db.update_broadcast_progress(cid, sent, failed, "completed")
        return {"campaign_id": cid, "total": total, "sent": sent, "failed": failed}

    async def get_heatmap_matrix(self, group_id: int) -> List[Dict[str, Any]]:
        """Retrieve 24x7 activity matrix for group."""
        return await self.db.get_activity_heatmap(group_id)

    async def get_ai_threats(self, group_id: int, limit: int = 30) -> List[Dict[str, Any]]:
        """Retrieve recent neural threat neutralization logs."""
        return await self.db.get_ai_threat_logs(group_id, limit)

    async def get_recent_messages(self, group_entity, limit: int = 30) -> List[Dict[str, Any]]:
        """Retrieve recent messages for live surveillance wiretap."""
        res = []
        try:
            messages = await self.client.get_messages(group_entity, limit=limit)
            for msg in reversed(messages):
                if not msg or not msg.text:
                    continue
                sender = await msg.get_sender()
                first_name = getattr(sender, 'first_name', '') or ''
                last_name = getattr(sender, 'last_name', '') or ''
                full_name = f"{first_name} {last_name}".strip() or getattr(sender, 'title', '') or "Anonymous"
                username = getattr(sender, 'username', '') or ''
                sender_id = getattr(sender, 'id', 0) if sender else 0
                
                res.append({
                    "message_id": msg.id,
                    "user_id": sender_id,
                    "full_name": full_name,
                    "username": username,
                    "text": msg.text,
                    "date": msg.date.strftime("%H:%M:%S") if msg.date else "",
                    "is_threat": False,
                    "threat_type": None,
                    "confidence": 0,
                    "reason": None
                })
        except Exception as e:
            logger.error(f"Error fetching recent messages: {e}")
        return res

    # ═══════════════════════════════════════════════════════
    #   TACTICAL VOICE CHAT // GROUP CALL APEX
    # ═══════════════════════════════════════════════════════
    async def _get_group_call_input(self, group_entity, force: bool = False) -> Optional[InputGroupCall]:
        """Helper to resolve active InputGroupCall with short TTL caching to prevent MTProto flood wait."""
        gid = getattr(group_entity, 'id', str(group_entity))
        if not hasattr(self, '_call_input_cache'):
            self._call_input_cache = {}
        if not force and gid in self._call_input_cache:
            entry = self._call_input_cache[gid]
            if time.time() - entry['time'] < 12:
                return entry['input']

        call = None
        try:
            full = await self.client(GetFullChannelRequest(group_entity))
            call = getattr(full.full_chat, 'call', None)
        except Exception:
            try:
                chat_id = group_entity.id if hasattr(group_entity, 'id') else group_entity
                full = await self.client(GetFullChatRequest(chat_id))
                call = getattr(full.full_chat, 'call', None)
            except Exception as e:
                logger.debug(f"Error resolving full chat call: {e}")
                call = None

        res = None
        if call:
            res = InputGroupCall(id=call.id, access_hash=call.access_hash)
        
        self._call_input_cache[gid] = {'input': res, 'time': time.time()}
        return res

    async def _ensure_pytgcalls(self):
        """Lazy-initialize PyTgCalls native WebRTC engine with TelegramClient."""
        if not HAS_PYTGCALLS:
            return None
        if self.call_py is None:
            self.call_py = PyTgCalls(self.client)
        if not self._call_py_started:
            try:
                await self.call_py.start()
                self._call_py_started = True
                logger.info("🎙️ PyTgCalls native WebRTC engine initialized.")
            except Exception as e:
                logger.warning(f"PyTgCalls startup note: {e}")
        return self.call_py

    def is_in_voice_call(self, group_id: Optional[int] = None) -> bool:
        """Check whether the client is currently connected to a voice chat."""
        if not self.active_vc_session:
            return False
        if group_id is not None:
            clean_active = str(self.active_vc_session.get('group_id')).replace('-100', '').replace('-', '')
            clean_target = str(group_id).replace('-100', '').replace('-', '')
            return clean_active == clean_target
        return True

    async def _vc_keepalive_loop(self, call_input: InputGroupCall, ssrc: int):
        """Periodically ping Telegram MTProto servers so the user account stays joined in the voice chat."""
        logger.info(f"🎙️ VC Keepalive loop active for SSRC {ssrc}")
        while self.active_vc_session and self.active_vc_session.get('ssrc') == ssrc:
            try:
                await asyncio.sleep(5)
                if not (self.active_vc_session and self.active_vc_session.get('ssrc') == ssrc):
                    break
                active_sources = await self.client(phone.CheckGroupCallRequest(
                    call=call_input,
                    sources=[ssrc]
                ))
                logger.debug(f"VC Keepalive ping OK: ssrc={ssrc}, active_sources={active_sources}")
            except asyncio.CancelledError:
                break
            except FloodWaitError as fe:
                await asyncio.sleep(fe.seconds + 1)
            except Exception as e:
                err_str = str(e).upper()
                if "GROUPCALL_INVALID" in err_str or "CALL_ALREADY_ENDED" in err_str:
                    logger.info("Voice call has ended on Telegram server.")
                    self.active_vc_session = None
                    break
                logger.debug(f"Keepalive ping note: {e}")

    async def join_voice_call(self, group_entity, muted: bool = True, webrtc_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Join the active voice chat as a sovereign listener / participant with full WebRTC audio exchange."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat detected in this group.")

        group_id = group_entity.id if hasattr(group_entity, 'id') else group_entity
        ssrc = (webrtc_params.get('ssrc') if webrtc_params else None) or random.randint(10000000, 99999999)

        # Check if already joined to this specific group call
        if self.is_in_voice_call(group_id):
            logger.info(f"🎙️ Already connected to voice chat in group {group_id}.")
            return {
                "success": True, 
                "status": "already_connected", 
                "group_id": group_id, 
                "muted": self.active_vc_session.get('muted', True) if self.active_vc_session else True, 
                "ssrc": self.active_vc_session.get('ssrc') if self.active_vc_session else ssrc,
                "pytgcalls": self.active_vc_session.get('joined_via_pytgcalls', True) if self.active_vc_session else True,
                "connection_data": self.active_vc_session.get('server_connection') if self.active_vc_session else None
            }

        # If connected to a different group voice chat, disconnect cleanly first
        if self.active_vc_session and not self.is_in_voice_call(group_id):
            logger.info("Switching voice chat: leaving prior group voice call first...")
            await self.leave_voice_call()

        # Convert group_entity to standard chat_id for PyTgCalls
        chat_id = group_entity
        if hasattr(group_entity, 'id'):
            if getattr(group_entity, 'broadcast', False) or getattr(group_entity, 'megagroup', False):
                chat_id = int(f"-100{group_entity.id}")
            else:
                chat_id = -group_entity.id
        elif isinstance(chat_id, int) and chat_id > 0:
            chat_id = int(f"-100{chat_id}")

        # Resolve exact authorized join_as peer
        join_as_peer = InputPeerSelf()
        try:
            join_as_res = await self.client(phone.GetGroupCallJoinAsRequest(peer=group_entity))
            if join_as_res and join_as_res.peers:
                user_peers = [p for p in join_as_res.peers if isinstance(p, types.InputPeerUser)]
                if user_peers:
                    join_as_peer = user_peers[0]
                else:
                    join_as_peer = join_as_res.peers[0]
        except Exception as e:
            logger.debug(f"JoinAs resolution note: {e}")

        # Cancel any previous keepalive
        if hasattr(self, '_vc_keepalive_task') and self._vc_keepalive_task and not self._vc_keepalive_task.done():
            self._vc_keepalive_task.cancel()

        server_conn = None
        joined_via_pytgcalls = False

        if webrtc_params:
            # Browser is requesting direct WebRTC live audio pipe
            if self.call_py and self._call_py_started:
                try:
                    await self.call_py.leave_call(chat_id)
                except Exception:
                    pass

            payload = dict(webrtc_params)
            if 'ssrc' not in payload:
                payload['ssrc'] = ssrc

            self.active_vc_session = {
                'group_id': group_id,
                'chat_id': chat_id,
                'call': call_input,
                'ssrc': ssrc,
                'muted': muted,
                'joined_at': time.time(),
                'join_as': join_as_peer,
                'joined_via_pytgcalls': False,
                'server_connection': None
            }

            params = DataJSON(data=json.dumps(payload))
            res = await self.client(phone.JoinGroupCallRequest(
                call=call_input,
                join_as=join_as_peer,
                params=params,
                muted=muted
            ))

            if hasattr(res, 'updates'):
                for u in res.updates:
                    if isinstance(u, types.UpdateGroupCallConnection):
                        try:
                            server_conn = json.loads(u.params.data)
                        except Exception:
                            server_conn = u.params.data
                        self.active_vc_session['server_connection'] = server_conn
                        break

            if not server_conn and self.active_vc_session:
                for _ in range(25):
                    if self.active_vc_session.get('server_connection'):
                        server_conn = self.active_vc_session['server_connection']
                        break
                    await asyncio.sleep(0.1)

            try:
                await self.client(phone.CheckGroupCallRequest(call=call_input, sources=[ssrc]))
            except Exception as e:
                logger.debug(f"Initial CheckGroupCall ping: {e}")

            self._vc_keepalive_task = asyncio.create_task(self._vc_keepalive_loop(call_input, ssrc))
            logger.info(f"🎙️ Connected to Group Voice Chat in group {group_id} via Direct WebRTC Audio Pipe (Server transport candidates: {len(server_conn.get('transport', {}).get('candidates', [])) if isinstance(server_conn, dict) else 0})")
        else:
            # Connect via PyTgCalls with live audio recording buffer for browser streaming
            clean_gid = str(group_id).replace("-100", "").replace("-", "")
            audio_stream_path = f"/tmp/vc_stream_{clean_gid}.mp3"
            if os.path.exists(audio_stream_path):
                try:
                    os.remove(audio_stream_path)
                except Exception:
                    pass

            # If our Telegram account is currently registered as joined in the call from a stale/prior session,
            # clear it on Telegram's SFU so PyTgCalls can establish its own fresh WebRTC transport cleanly.
            try:
                call_info = await self.get_group_call_info(group_entity)
                for p in call_info.get('participants', []):
                    if p.get('is_self') and p.get('source'):
                        logger.info(f"Clearing stale self session (source={p['source']}) on Telegram before PyTgCalls connect...")
                        await self.client(phone.LeaveGroupCallRequest(call=call_input, source=p['source']))
                        await asyncio.sleep(0.5)
                        break
            except Exception as e:
                logger.debug(f"Note clearing stale self participant: {e}")

            pycalls = await self._ensure_pytgcalls()
            if pycalls:
                try:
                    rs = RecordStream(audio=audio_stream_path)
                    await pycalls.record(chat_id, stream=rs)
                    joined_via_pytgcalls = True
                    if muted:
                        try:
                            await pycalls.mute(chat_id)
                        except Exception:
                            pass
                    logger.info(f"🎙️ Connected to Group Voice Chat in group {chat_id} via PyTgCalls (Live Audio Buffer: {audio_stream_path})")
                except Exception as e:
                    logger.warning(f"PyTgCalls record note (falling back to play): {e}")
                    try:
                        ms = MediaStream('', audio_flags=MediaStream.Flags.IGNORE, video_flags=MediaStream.Flags.IGNORE)
                        await pycalls.play(chat_id, stream=ms)
                        joined_via_pytgcalls = True
                    except Exception as e2:
                        logger.warning(f"PyTgCalls play fallback note: {e2}")

            if not joined_via_pytgcalls:
                ufrag = f"ufrag{random.randint(1000, 9999)}"
                pwd = f"pwd{random.randint(100000, 999999)}"
                fingerprint = "11:22:33:44:55:66:77:88:99:00:11:22:33:44:55:66:77:88:99:00:11:22:33:44:55:66:77:88:99:00:11:22"
                payload = {
                    'transport': {
                        'fingerprints': [{'hash': 'sha-256', 'fingerprint': fingerprint, 'setup': 'active'}],
                        'ufrag': ufrag,
                        'pwd': pwd
                    },
                    'ssrc': ssrc
                }

                self.active_vc_session = {
                    'group_id': group_id,
                    'chat_id': chat_id,
                    'call': call_input,
                    'ssrc': ssrc,
                    'muted': muted,
                    'joined_at': time.time(),
                    'join_as': join_as_peer,
                    'joined_via_pytgcalls': False,
                    'server_connection': None
                }

                params = DataJSON(data=json.dumps(payload))
                res = await self.client(phone.JoinGroupCallRequest(
                    call=call_input,
                    join_as=join_as_peer,
                    params=params,
                    muted=muted
                ))

                if hasattr(res, 'updates'):
                    for u in res.updates:
                        if isinstance(u, types.UpdateGroupCallConnection):
                            try:
                                server_conn = json.loads(u.params.data)
                            except Exception:
                                server_conn = u.params.data
                            self.active_vc_session['server_connection'] = server_conn
                            break

                try:
                    await self.client(phone.CheckGroupCallRequest(call=call_input, sources=[ssrc]))
                except Exception as e:
                    logger.debug(f"Initial CheckGroupCall ping: {e}")

                self._vc_keepalive_task = asyncio.create_task(self._vc_keepalive_loop(call_input, ssrc))
            else:
                self.active_vc_session = {
                    'group_id': group_id,
                    'chat_id': chat_id,
                    'call': call_input,
                    'ssrc': ssrc,
                    'muted': muted,
                    'joined_at': time.time(),
                    'join_as': join_as_peer,
                    'joined_via_pytgcalls': True,
                    'server_connection': None
                }

        return {
            "success": True, 
            "status": "connected", 
            "group_id": group_id, 
            "muted": muted, 
            "ssrc": ssrc,
            "pytgcalls": joined_via_pytgcalls,
            "connection_data": server_conn
        }

    async def leave_voice_call(self, group_entity = None) -> Dict[str, Any]:
        """Disconnect and leave the active voice chat cleanly."""
        if hasattr(self, '_vc_keepalive_task') and self._vc_keepalive_task and not self._vc_keepalive_task.done():
            self._vc_keepalive_task.cancel()

        if not self.active_vc_session:
            return {"success": True, "status": "not_connected"}

        chat_id = self.active_vc_session.get('chat_id')
        gid = self.active_vc_session.get('group_id')
        if chat_id and self.call_py and self._call_py_started:
            try:
                await self.call_py.leave_call(chat_id)
                logger.info(f"👋 Disconnected from Group Voice Chat {chat_id} via PyTgCalls.")
            except Exception as e:
                logger.debug(f"PyTgCalls leave_call note: {e}")

        if gid:
            clean_gid = str(gid).replace("-100", "").replace("-", "")
            for ext in [".mp3", ".wav"]:
                audio_path = f"/tmp/vc_stream_{clean_gid}{ext}"
                if os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception:
                        pass

        call_input = self.active_vc_session.get('call')
        ssrc = self.active_vc_session.get('ssrc')
        try:
            if call_input and ssrc:
                await self.client(phone.LeaveGroupCallRequest(call=call_input, source=ssrc))
        except Exception as e:
            logger.debug(f"Leave group call exception (safe to ignore): {e}")

        self.active_vc_session = None
        logger.info("👋 Disconnected from Group Voice Chat.")
        return {"success": True, "status": "disconnected"}

    async def get_group_call_info(self, group_entity, auto_join: bool = False) -> Dict[str, Any]:
        """Fetch active voice chat status, configuration, and complete participant profiles with pagination."""
        try:
            call_input = await self._get_group_call_input(group_entity)
            if not call_input:
                return {
                    "has_call": False,
                    "status": "INACTIVE",
                    "is_connected": False,
                    "message": "No voice chat active in this group."
                }

            group_id = group_entity.id if hasattr(group_entity, 'id') else group_entity
            is_connected = self.is_in_voice_call(group_id)

            # Auto-join if requested and not currently connected
            if auto_join and not is_connected:
                try:
                    await self.join_voice_call(group_entity, muted=True)
                    is_connected = True
                except Exception as e:
                    logger.warning(f"Auto-join to voice call failed: {e}")

            # Discover protected group admins to cross-reference
            admin_set = await self.get_admins(group_entity)

            # Discover current user ID for reliable self-detection
            me_id = None
            try:
                if not hasattr(self, '_me_id') or not self._me_id:
                    me = await self.client.get_me()
                    if me:
                        self._me_id = me.id
                me_id = getattr(self, '_me_id', None)
            except Exception:
                pass

            # Retrieve call details
            res = await self.client(phone.GetGroupCallRequest(call=call_input, limit=100))
            call = res.call

            if isinstance(call, GroupCallDiscarded) or not isinstance(call, GroupCall):
                return {
                    "has_call": False,
                    "status": "DISCARDED",
                    "is_connected": False,
                    "message": "Voice chat has ended."
                }

            # Gather all participants with complete pagination
            all_participants = list(res.participants)
            user_dict = {u.id: u for u in res.users}
            chat_dict = {c.id: c for c in getattr(res, 'chats', [])}
            offset = res.participants_next_offset or ""

            # Paginate through all members if call has more
            max_pages = 10
            pages = 0
            while offset and len(all_participants) < (call.participants_count or 1000) and pages < max_pages:
                pages += 1
                try:
                    p_res = await self.client(phone.GetGroupParticipantsRequest(
                        call=call_input, ids=[], sources=[], offset=offset, limit=100
                    ))
                    for u in p_res.users:
                        user_dict[u.id] = u
                    for c in getattr(p_res, 'chats', []):
                        chat_dict[c.id] = c
                    all_participants.extend(p_res.participants)
                    if not p_res.next_offset or p_res.next_offset == offset:
                        break
                    offset = p_res.next_offset
                except Exception as e:
                    logger.debug(f"Pagination error in VC participants: {e}")
                    break

            # Current UTC clock for real-time speech activity calculation
            now_utc = datetime.now(timezone.utc)

            # Format full forensic dossier for every participant
            participants = []
            for p in all_participants:
                uid = getattr(p.peer, 'user_id', None)
                if not uid and hasattr(p.peer, 'channel_id'):
                    uid = p.peer.channel_id
                
                u = user_dict.get(uid)
                c = chat_dict.get(uid) if not u else None

                first = getattr(u, 'first_name', '') or ''
                last = getattr(u, 'last_name', '') or ''
                name = f"{first} {last}".strip() if u else (getattr(c, 'title', '') or getattr(u, 'title', '') or f"User #{uid}")
                username = getattr(u or c, 'username', '') or ''
                phone_num = getattr(u, 'phone', '') or ''
                
                active_str = ""
                active_diff = None
                is_speaking = False
                just_spoke = False
                speech_status = "MUTED" if p.muted else "MIC OPEN (IDLE)"

                if getattr(p, 'active_date', None):
                    try:
                        active_str = p.active_date.strftime("%H:%M:%S")
                        active_dt = p.active_date
                        if active_dt.tzinfo is None:
                            active_dt = active_dt.replace(tzinfo=timezone.utc)
                        active_diff = max(0.0, round((now_utc - active_dt).total_seconds(), 1))

                        if not p.muted and active_diff <= 3.8:
                            is_speaking = True
                            speech_status = "TALKING NOW"
                        elif not p.muted and active_diff <= 8.0:
                            just_spoke = True
                            speech_status = "JUST SPOKE"
                        elif not p.muted:
                            speech_status = "MIC OPEN (IDLE)"
                    except Exception as e:
                        logger.debug(f"Speech calculation note: {e}")

                date_str = ""
                if getattr(p, 'date', None):
                    try:
                        date_str = p.date.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                is_admin = bool(uid in admin_set or getattr(p, 'volume_by_admin', False))

                if is_speaking and self.db:
                    asyncio.create_task(self.db.record_vc_speaker_activity(
                        group_id=group_id, call_id=str(call.id), user_id=uid,
                        full_name=name, username=username, delta_sec=2.0
                    ))

                participants.append({
                    "user_id": uid,
                    "full_name": name,
                    "first_name": first,
                    "last_name": last,
                    "username": username,
                    "phone": phone_num,
                    "is_bot": bool(getattr(u, 'bot', False)),
                    "is_premium": bool(getattr(u, 'premium', False)),
                    "is_verified": bool(getattr(u, 'verified', False)),
                    "is_scam": bool(getattr(u, 'scam', False)),
                    "is_fake": bool(getattr(u, 'fake', False)),
                    "is_admin": is_admin,
                    "is_self": bool(getattr(p, 'is_self', False) or (me_id and uid == me_id)),
                    "muted": bool(p.muted),
                    "is_speaking": is_speaking,
                    "just_spoke": just_spoke,
                    "speech_status": speech_status,
                    "active_diff": active_diff,
                    "can_self_unmute": bool(p.can_self_unmute),
                    "muted_by_you": bool(p.muted_by_you),
                    "volume": (p.volume or 10000) // 100,
                    "raise_hand": bool(p.raise_hand_rating),
                    "raise_hand_rating": getattr(p, 'raise_hand_rating', 0) or 0,
                    "video_joined": bool(p.video_joined),
                    "is_screen_sharing": bool(getattr(p, 'presentation', None)),
                    "source": getattr(p, 'source', 0) or 0,
                    "about": getattr(p, 'about', '') or '',
                    "active_date": active_str,
                    "date_joined": date_str
                })

            if not is_connected and any(p["is_self"] for p in participants):
                is_connected = True

            active_talkers = [p for p in participants if p["is_speaking"] or (p["just_spoke"] and p.get("active_diff", 99) <= 4.0)]

            talker_leaderboard = []
            if self.db:
                try:
                    talker_leaderboard = await self.db.get_vc_talker_leaderboard(group_id, str(call.id))
                except Exception:
                    pass

            return {
                "has_call": True,
                "status": "LIVE",
                "is_connected": is_connected,
                "call_id": call.id,
                "access_hash": call.access_hash,
                "title": call.title or "Group Voice Chat",
                "participants_count": max(call.participants_count, len(participants)),
                "loaded_count": len(participants),
                "join_muted": bool(call.join_muted),
                "can_change_join_muted": bool(call.can_change_join_muted),
                "record_active": bool(call.record_start_date),
                "active_talkers": active_talkers,
                "active_talkers_count": len(active_talkers),
                "talker_leaderboard": talker_leaderboard,
                "participants": participants
            }
        except Exception as e:
            logger.warning(f"Failed to fetch group call info: {e}")
            return {"has_call": False, "status": "ERROR", "is_connected": False, "error": str(e)}

    async def create_group_call(self, group_entity, title: str = "KAGE Tactical Voice Chamber", rtmp_stream: bool = False) -> Dict[str, Any]:
        """Initiate / Launch a Group Voice Chat as administrator."""
        random_id = random.randint(100000, 999999999)
        await self.client(phone.CreateGroupCallRequest(
            peer=group_entity,
            random_id=random_id,
            title=title,
            rtmp_stream=rtmp_stream
        ))
        logger.info(f"🎙️ Group Voice Chat launched: \"{title}\"")
        return {"success": True, "title": title}

    async def discard_group_call(self, group_entity) -> Dict[str, Any]:
        """Terminate / End the active Group Voice Chat."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat found to terminate.")
        await self.client(phone.DiscardGroupCallRequest(call=call_input))
        logger.info("🛑 Group Voice Chat terminated by operator.")
        return {"success": True, "status": "terminated"}

    async def edit_vc_participant(self, group_entity, user_id: int, muted: Optional[bool] = None, volume: Optional[int] = None, raise_hand: Optional[bool] = None) -> Dict[str, Any]:
        """Mute/unmute or adjust volume for a specific participant in voice chat."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat found.")
        participant = await self.client.get_input_entity(user_id)
        vol_arg = int(volume * 100) if volume is not None else None
        await self.client(phone.EditGroupCallParticipantRequest(
            call=call_input,
            participant=participant,
            muted=muted,
            volume=vol_arg,
            raise_hand=raise_hand
        ))
        return {"success": True, "user_id": user_id, "muted": muted, "volume": volume}

    async def toggle_vc_join_muted(self, group_entity, join_muted: bool) -> Dict[str, Any]:
        """Set voice chat entrance policy: auto-mute new joiners."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat found.")
        await self.client(phone.ToggleGroupCallSettingsRequest(call=call_input, join_muted=join_muted))
        return {"success": True, "join_muted": join_muted}

    async def mute_all_vc_participants(self, group_entity) -> Dict[str, Any]:
        """Nuclear Silence: Mute all non-admin participants in active voice chat and lock entrance."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat found.")
        
        # Lock entrance to muted
        try:
            await self.client(phone.ToggleGroupCallSettingsRequest(call=call_input, join_muted=True))
        except Exception:
            pass

        res = await self.client(phone.GetGroupCallRequest(call=call_input, limit=100))
        muted_count = 0
        for p in res.participants:
            if getattr(p, 'is_self', False):
                continue
            if not getattr(p, 'muted', False):
                try:
                    await self.client(phone.EditGroupCallParticipantRequest(
                        call=call_input,
                        participant=await self.client.get_input_entity(p.peer),
                        muted=True
                    ))
                    muted_count += 1
                except Exception as e:
                    logger.debug(f"Failed to mute participant: {e}")
        
        return {"success": True, "muted_count": muted_count}

    async def edit_vc_title(self, group_entity, title: str) -> Dict[str, Any]:
        """Update voice chat display title."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat found.")
        await self.client(phone.EditGroupCallTitleRequest(call=call_input, title=title))
        return {"success": True, "title": title}

    async def export_vc_invite(self, group_entity, can_self_unmute: bool = True) -> Dict[str, Any]:
        """Export direct Voice Chat join link."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat found.")
        inv = await self.client(phone.ExportGroupCallInviteRequest(call=call_input, can_self_unmute=can_self_unmute))
        return {"success": True, "link": inv.link}

    async def invite_to_vc(self, group_entity, user_ids: List[int]) -> Dict[str, Any]:
        """Mass-invite users to the voice chat."""
        call_input = await self._get_group_call_input(group_entity)
        if not call_input:
            raise ValueError("No active voice chat found.")
        users = []
        for uid in user_ids:
            try:
                users.append(await self.client.get_input_entity(uid))
            except Exception:
                pass
        if users:
            await self.client(phone.InviteToGroupCallRequest(call=call_input, users=users))
        return {"success": True, "invited": len(users)}

    # ═══════════════════════════════════════════════════════
    #   SOVEREIGN INTELLIGENCE & VC AUDIO FORENSICS
    # ═══════════════════════════════════════════════════════
    async def get_replay_audio_slice(self, group_id: int, seconds: int = 30) -> Optional[str]:
        """Extract the last N seconds of live voice chat audio for instant replay DVR."""
        clean_gid = str(group_id).replace("-100", "").replace("-", "")
        live_audio = f"/tmp/vc_stream_{clean_gid}.mp3"
        replay_audio = f"/tmp/vc_replay_{clean_gid}.mp3"

        if not os.path.exists(live_audio) or os.path.getsize(live_audio) < 4096:
            return None

        ffmpeg_bin = os.path.abspath("./venv/bin/ffmpeg")
        if not os.path.exists(ffmpeg_bin):
            ffmpeg_bin = "ffmpeg"

        cmd = [
            ffmpeg_bin, "-y",
            "-sseof", f"-{seconds}",
            "-i", live_audio,
            "-c", "copy",
            replay_audio
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        if os.path.exists(replay_audio) and os.path.getsize(replay_audio) > 0:
            return replay_audio
        return None

    async def get_group_sentiment_analysis(self, group_entity) -> Dict[str, Any]:
        """Deep linguistic sentiment and vibe diagnostics for any group without adminship."""
        try:
            messages = await self.client.get_messages(group_entity, limit=60)
            texts = [m.text for m in messages if m and m.text]
            
            if not texts:
                return {
                    "vibe": "IDLE", "sentiment": "NEUTRAL", "sample_size": 0,
                    "toxic_count": 0, "hype_count": 0, "buzzwords": [], "top_chatters": []
                }

            total_words = []
            toxic_count = 0
            hype_count = 0

            toxic_words = {'scam', 'fake', 'liar', 'idiot', 'ban', 'shit', 'fuck', 'bitch', 'scammer', 'rug', 'trash', 'stfu', 'clown'}
            hype_words = {'lfg', 'moon', 'pump', 'gem', 'great', 'awesome', 'love', 'fire', 'bullish', 'wow', 'nice', 'good', 'win'}
            stop_words = {'the', 'is', 'at', 'which', 'on', 'a', 'an', 'this', 'that', 'and', 'or', 'in', 'to', 'for', 'it', 'of',
                          'with', 'as', 'by', 'are', 'be', 'was', 'i', 'you', 'he', 'she', 'they', 'we', 'me', 'my', 'have', 'from',
                          'not', 'what', 'all', 'were', 'when', 'your', 'can', 'said', 'there', 'use', 'do', 'how', 'their', 'if',
                          'will', 'up', 'other', 'about', 'out', 'many', 'then', 'them', 'these', 'so', 'some', 'would', 'make', 'like'}

            user_counts = defaultdict(int)

            for m in messages:
                if not m or not m.text:
                    continue
                sender = await m.get_sender()
                s_name = getattr(sender, 'first_name', '') or getattr(sender, 'title', '') or 'User'
                if s_name:
                    user_counts[s_name] += 1

                clean_words = re.findall(r'\b[a-zA-Z]{3,15}\b', m.text.lower())
                for w in clean_words:
                    if w in toxic_words:
                        toxic_count += 1
                    if w in hype_words:
                        hype_count += 1
                    if w not in stop_words and len(w) > 3:
                        total_words.append(w)

            word_counts = Counter(total_words).most_common(8)
            buzzwords = [{"word": w, "count": c} for w, c in word_counts]

            total_sample = len(texts)
            tox_ratio = toxic_count / max(1, total_sample)
            hype_ratio = hype_count / max(1, total_sample)

            if tox_ratio > 0.15:
                vibe = "HOSTILE / TOXIC"
                sentiment = "CRITICAL"
            elif tox_ratio > 0.06:
                vibe = "HEATED / CONFLICT"
                sentiment = "ELEVATED"
            elif hype_ratio > 0.12:
                vibe = "HYPE / BULLISH"
                sentiment = "EXCITED"
            elif total_sample > 25:
                vibe = "HIGH VELOCITY CHAT"
                sentiment = "ACTIVE"
            else:
                vibe = "CHILL / CALM"
                sentiment = "PEACEFUL"

            top_chatters = [{"name": k, "count": v} for k, v in sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:5]]

            return {
                "vibe": vibe,
                "sentiment": sentiment,
                "sample_size": total_sample,
                "toxic_count": toxic_count,
                "hype_count": hype_count,
                "buzzwords": buzzwords,
                "top_chatters": top_chatters
            }
        except Exception as e:
            logger.debug(f"Sentiment analysis note: {e}")
            return {
                "vibe": "ANALYSIS_OFFLINE", "sentiment": "NEUTRAL",
                "sample_size": 0, "toxic_count": 0, "hype_count": 0,
                "buzzwords": [], "top_chatters": []
            }

    async def simulate_presence(self, group_entity: Any, action_type: str = "typing") -> Dict[str, Any]:
        """Broadcasts stealth presence actions (typing, record-audio, etc.) to target group."""
        try:
            valid_actions = {
                "typing": "typing",
                "audio": "record-audio",
                "video": "record-video",
                "document": "upload-document",
                "cancel": "cancel"
            }
            action_code = valid_actions.get(action_type, "typing")
            async with self.client.action(group_entity, action_code):
                await asyncio.sleep(2)
            return {"status": "success", "action": action_code}
        except Exception as e:
            logger.warning(f"Failed to simulate presence: {e}")
            return {"status": "error", "error": str(e)}

    async def test_siphon_ping(self) -> Dict[str, Any]:
        """Test autonomous Siphon channel by delivering a test intelligence dispatch to Saved Messages."""
        try:
            memo = (
                "🛸 <b>[SUPERBAN OS // AUTONOMOUS SIPHON CHECK]</b>\n"
                "───────────────────────────────\n"
                "✅ <b>Uplink Status:</b> ACTIVE (MTProto Verified)\n"
                "📱 <b>Delivery Target:</b> Telegram Saved Messages (<code>me</code>)\n"
                "🛡️ <b>Privilege Tier:</b> Sovereign Non-Admin Node\n"
                "⏱️ <b>Dispatch Timestamp:</b> <code>" + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "</code>\n"
                "───────────────────────────────\n"
                "<i>Autonomous wiretaps and crypto alerts will push directly here in real-time.</i>"
            )
            await self.client.send_message("me", memo, parse_mode="html")
            return {"status": "delivered", "destination": "Saved Messages (me)"}
        except Exception as e:
            logger.error(f"Siphon test ping failed: {e}")
            return {"status": "error", "error": str(e)}

    async def siphon_message_to_me(self, group_id: int, message_id: int) -> Dict[str, Any]:
        """Forward or copy any wiretapped message directly to Telegram Saved Messages ('me')."""
        try:
            entity = await self.get_group_entity(group_id)
            if not entity:
                return {"status": "error", "error": "Target group not found"}

            group_title = getattr(entity, 'title', '') or f"Chat #{group_id}"

            # Attempt native forward first
            try:
                await self.client.forward_messages('me', messages=message_id, from_peer=entity)
                return {"status": "delivered", "method": "forwarded", "destination": "Saved Messages (me)"}
            except Exception as fwd_err:
                logger.debug(f"Direct forward restricted ({fwd_err}), using direct content copy")
                # Fallback if chat prevents forwards (noforwards flag)
                msg = await self.client.get_messages(entity, ids=message_id)
                if not msg:
                    return {"status": "error", "error": "Message content could not be retrieved"}

                sender = msg.sender
                sender_name = "Anonymous User"
                sender_username = ""
                if sender:
                    fn = getattr(sender, 'first_name', '') or ''
                    ln = getattr(sender, 'last_name', '') or ''
                    sender_name = f"{fn} {ln}".strip() or "User"
                    sender_username = f"@{sender.username}" if getattr(sender, 'username', None) else ""

                siphon_card = (
                    "🛸 <b>[SUPERBAN OS // SIPHON EXTRACTION]</b>\n"
                    "───────────────────────────────\n"
                    f"📁 <b>Group:</b> {group_title}\n"
                    f"👤 <b>Author:</b> <code>{sender_name}</code> {sender_username}\n"
                    f"⏱️ <b>Date:</b> <code>{msg.date.strftime('%Y-%m-%d %H:%M:%S UTC') if msg.date else 'Live'}</code>\n"
                    "───────────────────────────────\n"
                    f"{msg.raw_text or '[Media / Non-Text Content]'}"
                )
                await self.client.send_message('me', siphon_card, parse_mode='html')
                return {"status": "delivered", "method": "mirrored", "destination": "Saved Messages (me)"}
        except Exception as e:
            logger.error(f"Failed to siphon message: {e}")
            return {"status": "error", "error": str(e)}




