import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
)

from core.config import Config
from core.engine import SuperBanEngine, OperationState
from core.forensics import ForensicInspector

logger = logging.getLogger("WebMissionControl")

STATIC_DIR = Path(__file__).resolve().parent / "static"

def create_app(engine: SuperBanEngine):
    app = FastAPI(title="SuperBan Mission Control", version="3.2")
    
    connected_clients = set()
    cached_members: Dict[int, list] = {}
    
    auth_state = {
        "phone": "",
        "phone_code_hash": ""
    }

    async def broadcast_ws(payload: dict):
        dead = set()
        for ws in connected_clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        for d in dead:
            connected_clients.discard(d)

    engine.register_listener(broadcast_ws)
    engine.sentinel.register_callback(broadcast_ws)

    # ═══════════════════════════════════════════════════════
    #   OWNER GATEKEEPER & VERIFICATION SYSTEM
    # ═══════════════════════════════════════════════════════
    OWNER_TOKENS: Dict[str, float] = {}

    @app.middleware("http")
    async def owner_guard_middleware(request: Request, call_next):
        path = request.url.path

        # Unprotected static routes and owner authentication
        if (
            path == "/"
            or path.startswith("/static")
            or path.startswith("/api/owner/")
            or path in ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png")
            or path == "/ws"
        ):
            return await call_next(request)

        token = request.headers.get("X-Owner-Token") or request.cookies.get("owner_token")
        pin_configured = bool(Config.get_owner_pin())
        
        # If no PIN configured, force setup via /api/owner/status or /api/owner/setup
        if not pin_configured:
            return JSONResponse(
                status_code=403,
                content={"detail": "Owner verification PIN not configured. Please initialize master PIN."}
            )

        if not token or token not in OWNER_TOKENS or OWNER_TOKENS[token] < time.time():
            return JSONResponse(
                status_code=401,
                content={"detail": "Owner clearance required. Access denied."}
            )

        return await call_next(request)

    class OwnerPinReq(BaseModel):
        pin: str

    @app.get("/api/owner/status")
    async def get_owner_status(request: Request):
        token = request.headers.get("X-Owner-Token") or request.cookies.get("owner_token")
        pin_configured = bool(Config.get_owner_pin())
        is_authenticated = bool(token and token in OWNER_TOKENS and OWNER_TOKENS[token] >= time.time())
        return {
            "authenticated": is_authenticated,
            "pin_configured": pin_configured
        }

    @app.post("/api/owner/setup")
    async def setup_owner_pin(req: OwnerPinReq, response: Response):
        current_pin = Config.get_owner_pin()
        if current_pin:
            raise HTTPException(status_code=400, detail="Owner PIN already configured. Please verify.")
        pin = req.pin.strip()
        if len(pin) < 4:
            raise HTTPException(status_code=400, detail="Owner PIN must be at least 4 characters.")
        Config.save_owner_pin(pin)
        token = secrets.token_hex(32)
        OWNER_TOKENS[token] = time.time() + (7 * 86400)
        response.set_cookie(key="owner_token", value=token, max_age=7*86400, samesite="lax")
        return {"status": "success", "token": token}

    @app.post("/api/owner/verify")
    async def verify_owner_pin(req: OwnerPinReq, response: Response):
        current_pin = Config.get_owner_pin()
        if not current_pin:
            raise HTTPException(status_code=400, detail="No PIN configured. Run setup first.")
        if req.pin.strip() != current_pin:
            raise HTTPException(status_code=403, detail="Invalid Owner PIN. Clearance Denied.")
        token = secrets.token_hex(32)
        OWNER_TOKENS[token] = time.time() + (7 * 86400)
        response.set_cookie(key="owner_token", value=token, max_age=7*86400, samesite="lax")
        return {"status": "authorized", "token": token}

    @app.post("/api/owner/logout")
    async def logout_owner(request: Request, response: Response):
        token = request.headers.get("X-Owner-Token") or request.cookies.get("owner_token")
        if token and token in OWNER_TOKENS:
            del OWNER_TOKENS[token]
        response.delete_cookie(key="owner_token")
        return {"status": "logged_out"}

    @app.get("/", response_class=HTMLResponse)
    async def get_index():
        index_file = STATIC_DIR / "index.html"
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="Index file not found")
        return FileResponse(index_file, headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        })

    # ═══════════════════════════════════════════════════════
    #   AUTHENTICATION API (IN-BROWSER TELEGRAM SIGN IN)
    # ═══════════════════════════════════════════════════════
    @app.get("/api/auth/status")
    async def get_auth_status():
        creds = Config.get_credentials()
        try:
            if not engine.client.is_connected():
                await engine.client.connect()
            is_auth = await engine.client.is_user_authorized()
            me = await engine.client.get_me() if is_auth else None
            return {
                "authorized": is_auth,
                "phone": creds["phone"] if creds else "",
                "user": {
                    "id": me.id if me else None,
                    "first_name": me.first_name if me else "",
                    "username": me.username if me else ""
                } if me else None
            }
        except Exception as e:
            return {"authorized": False, "error": str(e), "phone": creds["phone"] if creds else ""}

    @app.post("/api/auth/send_code")
    async def send_code():
        creds = Config.get_credentials()
        if not creds:
            raise HTTPException(status_code=400, detail="Missing TELEGRAM_PHONE or credentials in .env")
        try:
            if not engine.client.is_connected():
                await engine.client.connect()
            try:
                res = await engine.client.send_code_request(creds["phone"])
            except Exception as e:
                err_str = str(e).lower()
                if "different ip" in err_str or "auth_key" in err_str or "session" in err_str or "security" in err_str or "unregistered" in err_str:
                    logger.warning(f"Resetting invalid session due to: {e}")
                    try:
                        await engine.client.disconnect()
                    except Exception:
                        pass
                    engine.client = TelegramClient(StringSession(), creds["api_id"], creds["api_hash"])
                    await engine.client.connect()
                    res = await engine.client.send_code_request(creds["phone"])
                else:
                    raise e

            auth_state["phone"] = creds["phone"]
            auth_state["phone_code_hash"] = res.phone_code_hash
            logger.info(f"Telegram login code dispatched to {creds['phone']}")
            return {"status": "code_sent", "phone": creds["phone"]}
        except Exception as e:
            logger.error(f"Failed to send code: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VerifyCodeReq(BaseModel):
        code: str
        password: Optional[str] = None

    @app.post("/api/auth/verify_code")
    async def verify_code(req: VerifyCodeReq):
        try:
            if not engine.client.is_connected():
                await engine.client.connect()

            if req.password:
                await engine.client.sign_in(password=req.password)
            else:
                try:
                    await engine.client.sign_in(
                        phone=auth_state.get("phone") or Config.get_credentials()["phone"],
                        code=req.code,
                        phone_code_hash=auth_state.get("phone_code_hash")
                    )
                except SessionPasswordNeededError:
                    return {"status": "password_needed", "message": "2-Step Verification password is required."}

            # Export StringSession to .env permanently!
            saved_str = ""
            try:
                temp_s = StringSession()
                temp_s.set_dc(engine.client.session.dc_id, engine.client.session.server_address, engine.client.session.port)
                temp_s.auth_key = engine.client.session.auth_key
                saved_str = temp_s.save()
                if saved_str:
                    Config.save_string_session(saved_str)
                    logger.info("Session string permanently committed to .env")
            except Exception as e:
                logger.debug(f"Save string session note: {e}")

            # Force clear dialogs cache so groups load immediately
            try:
                await engine.get_dialogs(force=True)
            except Exception:
                pass

            me = await engine.client.get_me()
            return {
                "status": "authorized",
                "session_string": saved_str,
                "user": {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username
                }
            }
        except PhoneCodeInvalidError:
            raise HTTPException(status_code=400, detail="The code you entered is invalid. Please check your Telegram app.")
        except PhoneCodeExpiredError:
            raise HTTPException(status_code=400, detail="The code has expired. Please request a new one.")
        except Exception as e:
            logger.error(f"Sign in error: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    # ═══════════════════════════════════════════════════════
    #   OPERATIONAL ENDPOINTS
    # ═══════════════════════════════════════════════════════
    @app.get("/api/status")
    async def get_status():
        try:
            is_auth = await engine.client.is_user_authorized()
            me = await engine.client.get_me() if is_auth else None
            return {
                "authorized": is_auth,
                "account": {
                    "id": me.id if me else None,
                    "first_name": me.first_name if me else "",
                    "phone": me.phone if me else "",
                    "username": me.username if me else ""
                },
                "state": engine.state,
                "telemetry": engine.telemetry,
                "rate_limiter": engine.rate_limiter.get_status()
            }
        except Exception as e:
            return {"authorized": False, "error": str(e)}

    @app.get("/api/dialogs")
    async def get_dialogs():
        if not await engine.client.is_user_authorized():
            raise HTTPException(status_code=401, detail="Telegram account not authorized")
        groups = await engine.get_dialogs()
        return [{"id": g["id"], "title": g["title"], "members_count": g["members_count"]} for g in groups]

    class ScanRequest(BaseModel):
        group_id: int
        limit: int = 2000

    @app.post("/api/scan")
    async def scan_group(req: ScanRequest):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")

        try:
            members = await engine.inspect_group_members(entity, limit=req.limit)
            cached_members[req.group_id] = members
            return {"group_id": req.group_id, "total": len(members), "members": members}
        except Exception as e:
            logger.error(f"Scan group error: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    # ═══════════════════════════════════════════════════════
    #   SENTINEL RAID SHIELD & AUTONOMOUS DEFENSE
    # ═══════════════════════════════════════════════════════
    class SentinelToggleReq(BaseModel):
        group_id: int
        enabled: bool
        options: Optional[Dict[str, Any]] = None

    @app.post("/api/sentinel/toggle")
    async def toggle_sentinel(req: SentinelToggleReq):
        if req.enabled:
            engine.sentinel.enable_shield(req.group_id, req.options)
        else:
            engine.sentinel.disable_shield(req.group_id)
        return {
            "group_id": req.group_id,
            "active": engine.sentinel.is_shield_active(req.group_id),
            "stats": engine.sentinel.stats
        }

    @app.get("/api/sentinel/status")
    async def get_sentinel_status():
        return {
            "active_groups": list(engine.sentinel.active_groups.keys()),
            "stats": engine.sentinel.stats
        }

    class LockdownReq(BaseModel):
        group_id: int

    @app.post("/api/sentinel/lockdown")
    async def lockdown_chat(req: LockdownReq):
        success = await engine.sentinel.lockdown_group(req.group_id)
        return {"success": success, "group_id": req.group_id, "status": "locked"}

    @app.post("/api/sentinel/unlock")
    async def unlock_chat(req: LockdownReq):
        success = await engine.sentinel.unlock_group(req.group_id)
        return {"success": success, "group_id": req.group_id, "status": "unlocked"}

    class ChatterAnalysisReq(BaseModel):
        group_id: int
        limit: int = 1500

    @app.post("/api/analytics/chatters")
    async def analyze_chatters(req: ChatterAnalysisReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")

        report = await engine.sentinel.analyze_chat_activity(entity, limit=req.limit)
        return report

    # ═══════════════════════════════════════════════════════
    #   SERVICE MESSAGES, LEADS SCRAPER, & STRIKE SYSTEM
    # ═══════════════════════════════════════════════════════
    class ServiceMessageCleanReq(BaseModel):
        group_id: int
        limit: int = 2500

    @app.post("/api/service_messages/clean")
    async def clean_service_msgs(req: ServiceMessageCleanReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")

        purged_count = await engine.clean_service_messages(entity, limit=req.limit)
        return {"purged": purged_count, "group_id": req.group_id}

    class ScrapeLeadsReq(BaseModel):
        group_id: int
        active_only: bool = True
        limit: int = 3000

    @app.post("/api/leads/scrape")
    async def scrape_leads(req: ScrapeLeadsReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")

        try:
            leads = await engine.scrape_leads(entity, active_only=req.active_only, limit=req.limit)
            return {"total": len(leads), "leads": leads, "group_id": req.group_id}
        except Exception as e:
            logger.error(f"Scrape leads error: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class WarningReq(BaseModel):
        user_id: int
        group_id: int
        reason: str = "Spam / Terms Violation"

    @app.post("/api/warnings/add")
    async def add_warning(req: WarningReq):
        strikes = await engine.db.add_warning(req.user_id, req.group_id, req.reason)
        auto_banned = False
        if strikes >= 3:
            # Auto-ban on 3 strikes
            entity = await engine.get_group_entity(req.group_id)
            if entity:
                await engine.execute_action_on_user_id(entity, req.user_id, "ban", purge_messages=True)
                auto_banned = True
        return {"user_id": req.user_id, "strikes": strikes, "auto_banned": auto_banned}

    @app.get("/api/warnings")
    async def get_warnings(group_id: int):
        return await engine.db.get_warnings(group_id)

    # ═══════════════════════════════════════════════════════
    #   DEFCON DEFENSE MATRIX & AI THREAT NEUTRALIZER
    # ═══════════════════════════════════════════════════════
    class DefconReq(BaseModel):
        group_id: int
        level: int
        slowmode_seconds: int = 0

    @app.post("/api/defcon")
    async def set_defcon(req: DefconReq):
        lvl = await engine.sentinel.set_defcon_level(req.group_id, req.level, req.slowmode_seconds)
        return {"status": "ok", "group_id": req.group_id, "level": lvl}

    @app.get("/api/defcon")
    async def get_defcon(group_id: int):
        return await engine.sentinel.get_defcon_level(group_id)

    @app.get("/api/ai_threats")
    async def get_threats(group_id: int, limit: int = 30):
        threats = await engine.get_ai_threats(group_id, limit)
        return {"group_id": group_id, "total": len(threats), "threats": threats}

    @app.get("/api/analytics/heatmap")
    async def get_heatmap(group_id: int):
        matrix = await engine.get_heatmap_matrix(group_id)
        return {"group_id": group_id, "matrix": matrix}

    class BroadcastReq(BaseModel):
        group_id: int
        target_ids: list[int]
        message: str

    @app.post("/api/broadcast")
    async def start_broadcast(req: BroadcastReq):
        members = cached_members.get(req.group_id, [])
        target_dict = {m["id"]: m for m in members}
        targets = [target_dict[tid] for tid in req.target_ids if tid in target_dict]
        if not targets:
            targets = [{"id": tid, "username": "", "full_name": ""} for tid in req.target_ids]

        asyncio.create_task(engine.broadcast_campaign(targets, req.message))
        return {"status": "started", "total_targets": len(targets)}

    @app.get("/api/broadcast/campaigns")
    async def list_campaigns():
        return await engine.db.get_broadcast_campaigns()

    # ═══════════════════════════════════════════════════════
    #   AI COPILOT & REAL-TIME WIRETAP ACTIONS
    # ═══════════════════════════════════════════════════════
    class CopilotReq(BaseModel):
        prompt: str
        group_id: int

    @app.post("/api/copilot/execute")
    async def copilot_execute(req: CopilotReq):
        prompt = req.prompt.strip().lower()
        gid = req.group_id
        
        if "lockdown" in prompt or "mute all" in prompt:
            await engine.sentinel.set_defcon_level(gid, 1)
            return {"action": "defcon", "level": 1, "reply": "🚨 Nuclear lockdown engaged. All standard member permissions muted."}
        elif "unlock" in prompt or "peace" in prompt:
            await engine.sentinel.set_defcon_level(gid, 5)
            return {"action": "defcon", "level": 5, "reply": "🕊️ Chat unlocked. DEFCON 5 peace posture restored."}
        elif "defcon 2" in prompt or "raid" in prompt:
            await engine.sentinel.set_defcon_level(gid, 2)
            return {"action": "defcon", "level": 2, "reply": "⚠️ DEFCON 2 engaged. High-frequency anti-raid bouncer active."}
        elif "sweep" in prompt or "clean" in prompt or "purge service" in prompt:
            entity = await engine.get_group_entity(gid)
            if entity:
                purged = await engine.clean_service_messages(entity, limit=1500)
                return {"action": "sweep", "reply": f"🧹 Service sweeper purged {purged} join/leave clutter messages."}
            return {"action": "sweep", "reply": "Select a group to sweep."}
        elif "ghost" in prompt:
            return {"action": "filter", "filter": "ghosts", "reply": "👻 Filtering heuristic dossier to ghost accounts (score ≥ 60)."}
        elif "premium" in prompt or "lead" in prompt:
            return {"action": "filter", "filter": "premium", "reply": "💎 High-value Telegram Premium leads selected."}
        elif "start vc" in prompt or "launch vc" in prompt or "create vc" in prompt:
            entity = await engine.get_group_entity(gid)
            if entity:
                res = await engine.create_group_call(entity, title="KAGE Apex Tactical Briefing")
                return {"action": "vc_created", "reply": "🎙️ Group Voice Chat launched: \"KAGE Apex Tactical Briefing\""}
            return {"action": "error", "reply": "Target group not found."}
        elif "end vc" in prompt or "kill vc" in prompt or "stop vc" in prompt or "terminate vc" in prompt:
            entity = await engine.get_group_entity(gid)
            if entity:
                await engine.discard_group_call(entity)
                return {"action": "vc_discarded", "reply": "🛑 Active Group Voice Chat terminated by AI directive."}
            return {"action": "error", "reply": "Target group not found."}
        elif "mute vc" in prompt or "silence vc" in prompt:
            entity = await engine.get_group_entity(gid)
            if entity:
                res = await engine.mute_all_vc_participants(entity)
                return {"action": "vc_muted", "reply": f"🤫 Silenced {res['muted_count']} participants in Voice Chat and enabled join-mute lock."}
            return {"action": "error", "reply": "Target group not found."}
        elif "join vc" in prompt or "connect vc" in prompt or "listen vc" in prompt:
            entity = await engine.get_group_entity(gid)
            if entity:
                res = await engine.join_voice_call(entity, muted=True)
                return {"action": "vc_joined", "reply": "🎧 Connected to Group Voice Chat as Ghost Listener (Muted). Live audio stream active."}
            return {"action": "error", "reply": "Target group not found."}
        elif "leave vc" in prompt or "disconnect vc" in prompt or "exit vc" in prompt:
            res = await engine.leave_voice_call()
            return {"action": "vc_left", "reply": "👋 Disconnected from Voice Chat frequency."}
        elif "vc" in prompt or "voice" in prompt:
            entity = await engine.get_group_entity(gid)
            if entity:
                info = await engine.get_group_call_info(entity)
                if info.get("has_call"):
                    connected_note = " (Connected)" if info.get("is_connected") else " (Remote Radar)"
                    return {"action": "vc_info", "reply": f"🎙️ VC Live: \"{info.get('title')}\"{connected_note} with {info.get('participants_count', 0)} active participants. Join-muted: {info.get('join_muted')}."}
                else:
                    return {"action": "vc_info", "reply": "🔇 No active Voice Chat detected in this group. Directive available: \"start vc\""}
            return {"action": "error", "reply": "Target group not found."}
        elif "stats" in prompt or "status" in prompt:
            stats = await engine.db.get_stats()
            return {"action": "stats", "data": stats, "reply": f"📊 Stats: {stats['total_actions']} actions executed, {stats['completed_sessions']} sessions completed, {stats['scraped_leads']} leads indexed."}
        else:
            return {"action": "info", "reply": f"🤖 AI Copilot processed directive: \"{req.prompt}\". Analyzing group heuristics."}

    @app.get("/api/messages")
    async def get_messages(group_id: int, limit: int = 35):
        try:
            entity = await engine.get_group_entity(group_id)
            if not entity:
                return []
            msgs = await engine.get_recent_messages(entity, limit=limit)
            return msgs
        except Exception as e:
            logger.error(f"Failed to fetch messages for group {group_id}: {e}")
            return []

    class DeleteMsgReq(BaseModel):
        group_id: int
        message_id: int

    @app.post("/api/message/delete")
    async def delete_message(req: DeleteMsgReq):
        try:
            entity = await engine.client.get_entity(req.group_id)
            await engine.client.delete_messages(entity, req.message_id)
            return {"status": "deleted", "message_id": req.message_id}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    class SingleUserActionReq(BaseModel):
        group_id: int
        user_id: int
        action: str = "ban"
        purge: bool = False

    @app.post("/api/user_action")
    async def single_user_action(req: SingleUserActionReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")

        success = await engine.execute_action_on_user_id(
            entity, req.user_id, req.action, purge_messages=req.purge
        )
        return {"success": success, "user_id": req.user_id, "action": req.action}

    class OperationRequest(BaseModel):
        group_id: int
        target_ids: list[int]
        action: str = "ban"
        mode: str = "normal"
        purge_messages: bool = False
        skip_admins: bool = True
        duration_seconds: int = 0
        permission_subtype: str = "full"

    @app.post("/api/execute")
    async def execute_operation(req: OperationRequest):
        if engine.state == OperationState.RUNNING:
            raise HTTPException(status_code=400, detail="An operation is already in progress")

        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")

        members = cached_members.get(req.group_id, [])
        target_dict = {m["id"]: m for m in members}
        
        targets = [target_dict[tid] for tid in req.target_ids if tid in target_dict]
        if not targets:
            targets = [{"id": tid, "username": "", "full_name": ""} for tid in req.target_ids]

        asyncio.create_task(engine.run_operation(
            entity, targets, 
            action=req.action, mode=req.mode, 
            purge_messages=req.purge_messages, skip_admins=req.skip_admins,
            duration_seconds=req.duration_seconds, permission_subtype=req.permission_subtype
        ))
        return {"status": "started", "target_count": len(targets)}

    @app.post("/api/control/pause")
    async def pause_op():
        engine.pause()
        return {"state": engine.state}

    @app.post("/api/control/resume")
    async def resume_op():
        engine.resume()
        return {"state": engine.state}

    @app.post("/api/control/abort")
    async def abort_op():
        engine.abort()
        return {"state": engine.state}

    # ═══════════════════════════════════════════════════════
    #   TACTICAL VOICE CHAT // VC COMMAND APEX
    # ═══════════════════════════════════════════════════════
    @app.get("/api/vc/status")
    async def get_vc_status(group_id: int, auto_join: bool = False):
        entity = await engine.get_group_entity(group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        return await engine.get_group_call_info(entity, auto_join=auto_join)

    class VCJoinReq(BaseModel):
        group_id: int
        muted: bool = True
        webrtc_params: Optional[Dict[str, Any]] = None

    @app.post("/api/vc/join")
    async def join_vc(req: VCJoinReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.join_voice_call(entity, muted=req.muted, webrtc_params=req.webrtc_params)
        except Exception as e:
            logger.error(f"Failed to join VC: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/vc/connection")
    async def get_vc_connection():
        if not engine.active_vc_session:
            return {"connected": False, "connection_data": None}
        return {
            "connected": True,
            "group_id": engine.active_vc_session.get("group_id"),
            "ssrc": engine.active_vc_session.get("ssrc"),
            "connection_data": engine.active_vc_session.get("server_connection")
        }

    @app.head("/api/vc/audio_stream")
    async def vc_audio_stream_head(group_id: int):
        clean_gid = str(group_id).replace("-100", "").replace("-", "")
        audio_path = f"/tmp/vc_stream_{clean_gid}.mp3"
        return Response(
            status_code=200 if os.path.exists(audio_path) else 404,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Accept-Ranges": "none",
                "Access-Control-Allow-Origin": "*"
            }
        )

    @app.get("/api/vc/audio_stream")
    async def vc_audio_stream(group_id: int):
        clean_gid = str(group_id).replace("-100", "").replace("-", "")
        audio_path = f"/tmp/vc_stream_{clean_gid}.mp3"

        # Wait up to 10 seconds for the audio stream buffer to initialize
        for _ in range(100):
            if os.path.exists(audio_path):
                break
            await asyncio.sleep(0.1)

        if not os.path.exists(audio_path):
            raise HTTPException(status_code=404, detail="No active voice chat audio stream found on server")

        async def stream_generator():
            try:
                with open(audio_path, "rb") as f:
                    # Low-latency seek: jump close to live head if file has already grown
                    try:
                        cur_sz = os.path.getsize(audio_path)
                        if cur_sz > 32768:
                            f.seek(cur_sz - 16384)
                    except Exception:
                        pass

                    while engine.is_in_voice_call(group_id):
                        chunk = f.read(4096)
                        if chunk:
                            yield chunk
                        else:
                            await asyncio.sleep(0.04)
                    leftover = f.read()
                    if leftover:
                        yield leftover
            except (asyncio.CancelledError, GeneratorExit):
                pass
            except Exception as e:
                logger.debug(f"Audio stream generator note: {e}")

        return StreamingResponse(
            stream_generator(),
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Accept-Ranges": "none",
                "Access-Control-Allow-Origin": "*"
            }
        )

    class VCLeaveReq(BaseModel):
        group_id: Optional[int] = None

    @app.post("/api/vc/leave")
    async def leave_vc(req: VCLeaveReq):
        try:
            return await engine.leave_voice_call()
        except Exception as e:
            logger.error(f"Failed to leave VC: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VCCreateReq(BaseModel):
        group_id: int
        title: str = "KAGE Tactical Voice Chamber"
        rtmp_stream: bool = False

    @app.post("/api/vc/create")
    async def create_vc(req: VCCreateReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.create_group_call(entity, title=req.title, rtmp_stream=req.rtmp_stream)
        except Exception as e:
            logger.error(f"Failed to create group call: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VCDiscardReq(BaseModel):
        group_id: int

    @app.post("/api/vc/discard")
    async def discard_vc(req: VCDiscardReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.discard_group_call(entity)
        except Exception as e:
            logger.error(f"Failed to discard group call: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VCParticipantReq(BaseModel):
        group_id: int
        user_id: int
        muted: Optional[bool] = None
        volume: Optional[int] = None
        raise_hand: Optional[bool] = None

    @app.post("/api/vc/participant")
    async def edit_vc_participant(req: VCParticipantReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.edit_vc_participant(
                entity, req.user_id,
                muted=req.muted, volume=req.volume, raise_hand=req.raise_hand
            )
        except Exception as e:
            logger.error(f"Failed to edit VC participant: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VCSettingsReq(BaseModel):
        group_id: int
        join_muted: bool

    @app.post("/api/vc/settings")
    async def toggle_vc_settings(req: VCSettingsReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.toggle_vc_join_muted(entity, req.join_muted)
        except Exception as e:
            logger.error(f"Failed to update VC settings: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VCMuteAllReq(BaseModel):
        group_id: int

    @app.post("/api/vc/mute_all")
    async def mute_all_vc(req: VCMuteAllReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.mute_all_vc_participants(entity)
        except Exception as e:
            logger.error(f"Failed to mute all VC participants: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VCTitleReq(BaseModel):
        group_id: int
        title: str

    @app.post("/api/vc/title")
    async def edit_vc_title(req: VCTitleReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.edit_vc_title(entity, req.title)
        except Exception as e:
            logger.error(f"Failed to edit VC title: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/vc/invite_link")
    async def get_vc_invite_link(group_id: int, can_self_unmute: bool = True):
        entity = await engine.get_group_entity(group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.export_vc_invite(entity, can_self_unmute=can_self_unmute)
        except Exception as e:
            logger.error(f"Failed to export VC invite: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    class VCInviteReq(BaseModel):
        group_id: int
        user_ids: List[int]

    @app.post("/api/vc/invite")
    async def invite_users_to_vc(req: VCInviteReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        try:
            return await engine.invite_to_vc(entity, req.user_ids)
        except Exception as e:
            logger.error(f"Failed to invite users to VC: {e}")
            raise HTTPException(status_code=400, detail=str(e))

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        connected_clients.add(websocket)
        try:
            await websocket.send_json({"type": "initial", "telemetry": engine.telemetry})
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            connected_clients.discard(websocket)
        except Exception:
            connected_clients.discard(websocket)

    # ═══════════════════════════════════════════════════════
    #   SOVEREIGN NON-ADMIN INTELLIGENCE & SPYCRAFT SUITE
    # ═══════════════════════════════════════════════════════
    @app.get("/api/ghost/messages")
    async def get_ghost_messages(group_id: Optional[int] = None, filter_mode: str = 'all', limit: int = 50):
        if not engine.db:
            return []
        return await engine.db.get_ghost_messages(group_id=group_id, filter_mode=filter_mode, limit=limit)

    @app.get("/api/vc/replay")
    async def get_vc_replay(group_id: int, seconds: int = 30):
        slice_path = await engine.get_replay_audio_slice(group_id, seconds=seconds)
        if not slice_path or not os.path.exists(slice_path):
            raise HTTPException(status_code=404, detail="No active replay buffer available for this group")
        return FileResponse(slice_path, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})

    @app.get("/api/vc/download_recording")
    async def download_vc_recording(group_id: int):
        clean_gid = str(group_id).replace("-100", "").replace("-", "")
        live_path = f"/tmp/vc_stream_{clean_gid}.mp3"
        if not os.path.exists(live_path):
            raise HTTPException(status_code=404, detail="No voice chat recording found for this group")
        return FileResponse(
            live_path,
            media_type="audio/mpeg",
            filename=f"VC_Recording_{clean_gid}_{int(time.time())}.mp3"
        )

    @app.get("/api/vc/leaderboard")
    async def get_vc_leaderboard(group_id: int, call_id: Optional[str] = None):
        if not engine.db:
            return []
        return await engine.db.get_vc_talker_leaderboard(group_id, call_id=call_id)

    @app.get("/api/osint/user")
    async def get_user_osint(user_id: int):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        profile = await engine.db.get_user_cross_profile(user_id)
        if not profile:
            try:
                u = await engine.client.get_entity(user_id)
                full_name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
                username = getattr(u, 'username', '')
                profile = {
                    "user_id": user_id,
                    "first_seen": "Observed via radar",
                    "last_seen": "Active",
                    "current_name": full_name or f"User #{user_id}",
                    "current_username": username,
                    "name_history": [full_name] if full_name else [],
                    "username_history": [username] if username else [],
                    "spotted_groups": [],
                    "message_count": 0,
                    "vc_participations": 0,
                    "is_premium": bool(getattr(u, 'premium', False)),
                    "is_verified": bool(getattr(u, 'verified', False)),
                    "phone": getattr(u, 'phone', '') or ''
                }
            except Exception:
                raise HTTPException(status_code=404, detail="Target user profile not found in radar")
        return profile

    @app.get("/api/surveillance/watchlist")
    async def get_surveillance_watchlist():
        if not engine.db:
            return []
        return await engine.db.get_watchlist()

    class WatchlistAddReq(BaseModel):
        target_type: str # 'keyword' or 'user'
        value: str
        note: str = ""

    @app.post("/api/surveillance/watchlist")
    async def add_surveillance_watchlist(req: WatchlistAddReq):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        return await engine.db.add_watchlist_item(target_type=req.target_type, value=req.value, note=req.note)

    @app.delete("/api/surveillance/watchlist")
    async def delete_surveillance_watchlist(id: int):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        success = await engine.db.delete_watchlist_item(id)
        return {"success": success}

    @app.get("/api/surveillance/alerts")
    async def get_surveillance_alerts(limit: int = 50):
        if not engine.db:
            return []
        return await engine.db.get_surveillance_alerts(limit=limit)

    @app.get("/api/analytics/sentiment")
    async def get_group_sentiment(group_id: int):
        entity = await engine.get_group_entity(group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        return await engine.get_group_sentiment_analysis(entity)

    # =========================================================================
    #  SIGNAL & IOC ENTITY RADAR ENDPOINTS
    # =========================================================================
    @app.get("/api/entities/radar")
    async def get_entities_radar(group_id: Optional[int] = None, entity_type: Optional[str] = None, limit: int = 100):
        if not engine.db:
            return {"items": [], "stats": {}, "total": 0}
        return await engine.db.get_extracted_entities(group_id=group_id, entity_type=entity_type, limit=limit)

    # =========================================================================
    #  SOCIAL RELATIONSHIP NETWORK GRAPH ENDPOINTS
    # =========================================================================
    @app.get("/api/graph/social")
    async def get_social_graph(group_id: Optional[int] = None, limit: int = 150):
        if not engine.db:
            return {"nodes": [], "links": [], "top_influencers": [], "edge_count": 0, "node_count": 0}
        return await engine.db.get_social_graph(group_id=group_id, limit=limit)

    # =========================================================================
    #  AUTONOMOUS SIGNAL SIPHON & FORWARDING ENDPOINTS
    # =========================================================================
    class SiphonRuleReq(BaseModel):
        rule_name: str
        match_type: str # 'keyword', 'crypto', 'user', 'all_deleted'
        match_value: str = ""
        destination: str = "me"

    @app.get("/api/siphon/rules")
    async def get_siphon_rules():
        if not engine.db:
            return []
        return await engine.db.get_siphon_rules()

    @app.post("/api/siphon/rules")
    async def add_siphon_rule(req: SiphonRuleReq):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        return await engine.db.add_siphon_rule(
            rule_name=req.rule_name,
            match_type=req.match_type,
            match_value=req.match_value,
            destination=req.destination or "me"
        )

    @app.delete("/api/siphon/rules")
    async def delete_siphon_rule(id: int):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        success = await engine.db.delete_siphon_rule(id)
        return {"success": success}

    @app.post("/api/siphon/test")
    async def test_siphon():
        return await engine.test_siphon_ping()

    @app.get("/api/siphon/logs")
    async def get_siphon_logs(limit: int = 50):
        if not engine.db:
            return []
        return await engine.db.get_siphon_logs(limit=limit)

    # =========================================================================
    #  GLOBAL FORENSIC FULL-TEXT SEARCH
    # =========================================================================
    @app.get("/api/search/forensic")
    async def forensic_search(q: str, group_id: Optional[int] = None, limit: int = 100):
        if not engine.db:
            return []
        return await engine.db.global_forensic_search(query=q, group_id=group_id, limit=limit)

    # =========================================================================
    #  STEALTH PRESENCE EMULATOR
    # =========================================================================
    class PresenceReq(BaseModel):
        group_id: int
        action: str = "typing" # 'typing', 'audio', 'video', 'document', 'cancel'

    @app.post("/api/presence/simulate")
    async def simulate_presence(req: PresenceReq):
        entity = await engine.get_group_entity(req.group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        return await engine.simulate_presence(entity, req.action)

    @app.post("/api/terminal/launch")
    async def launch_native_terminal():
        import subprocess
        try:
            cmd_path = "/Users/archfiend/.gemini/antigravity/scratch/telegram_group_manager/launch_bot.command"
            subprocess.Popen(["open", cmd_path])
            return {"status": "launched", "message": "Mac Terminal.app opened with Afterlight Console."}
        except Exception as e:
            logger.error(f"Failed to launch terminal: {e}")
            return {"status": "error", "message": str(e)}

    return app
