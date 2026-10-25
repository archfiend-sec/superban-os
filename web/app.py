import asyncio
import csv
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiohttp
from telethon.sessions import StringSession
from telethon.tl.functions.messages import GetCommonChatsRequest
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
)

from core.config import Config
from core.engine import SuperBanEngine, OperationState
from core.forensics import ForensicInspector
from core.intel import IntelSynthesizer

logger = logging.getLogger("WebMissionControl")

STATIC_DIR = Path(__file__).resolve().parent / "static"
BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_VAULT_DIR = BASE_DIR / "media_vault"
OPERATOR_SESSIONS: Dict[str, Dict[str, Any]] = {} # token -> {username, role, expires}

# Ensure virtual environment bin (with ffmpeg) and common paths are prioritized
_venv_bin = str(BASE_DIR / "venv" / "bin")
for _p in [_venv_bin, "/usr/local/bin", "/opt/homebrew/bin"]:
    if os.path.exists(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{_p}:{os.environ.get('PATH', '')}"

_SILENCE_CACHE: Optional[bytes] = None

def get_mp3_silence_chunk() -> bytes:
    """Produce clean MPEG-1 Layer 3 48kHz 128kbps stereo silence frames with no ID3/Xing header."""
    global _SILENCE_CACHE
    if _SILENCE_CACHE is not None:
        return _SILENCE_CACHE
    try:
        candidates = [
            shutil.which("ffmpeg"),
            str(BASE_DIR / "venv" / "bin" / "ffmpeg"),
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg"
        ]
        ffmpeg_bin = next((c for c in candidates if c and os.path.exists(c)), "ffmpeg")
        cmd = [
            ffmpeg_bin, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "0.24", "-codec:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0",
            "-id3v2_version", "0", "-f", "mp3", "pipe:1"
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4)
        if proc.returncode == 0 and proc.stdout:
            _SILENCE_CACHE = proc.stdout
            return _SILENCE_CACHE
    except Exception as e:
        logger.debug(f"Silence generation note: {e}")
    return b""

def find_mp3_sync_offset(filepath: str, target_pos: int, max_scan: int = 4096) -> int:
    """Find the nearest valid MPEG Audio frame sync boundary starting at target_pos."""
    try:
        with open(filepath, "rb") as f:
            f.seek(max(0, target_pos))
            chunk = f.read(max_scan)
            if not chunk:
                return max(0, target_pos)
            for i in range(len(chunk) - 1):
                # 11 bits sync word (0xFFE0) + MPEG Layer III (0x02)
                if chunk[i] == 0xFF and (chunk[i+1] & 0xE0) == 0xE0 and (chunk[i+1] & 0x06) == 0x02:
                    return max(0, target_pos) + i
    except Exception:
        pass
    return max(0, target_pos)

def create_app(engine: SuperBanEngine):
    enable_docs = os.environ.get("ENABLE_API_DOCS", "0").strip().lower() in ("1", "true", "yes")
    app = FastAPI(
        title="SuperBan Mission Control",
        version="3.2",
        docs_url="/api/docs" if enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if enable_docs else None
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|superban-os\.onrender\.com)(:[0-9]+)?$",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Owner-Token"],
    )
    
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
    #   OWNER & OPERATOR MULTI-USER CLEARANCE REGISTRY
    # ═══════════════════════════════════════════════════════
    OWNER_TOKENS: Dict[str, float] = {}
    global OPERATOR_SESSIONS
    FAILED_VERIFY_ATTEMPTS: Dict[str, Dict[str, Any]] = {}
    OPERATIONAL_RATE_LIMITS: Dict[str, List[float]] = {}

    def get_client_ip(req: Request) -> str:
        forwarded = req.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return req.client.host if req.client else "127.0.0.1"

    @app.middleware("http")
    async def security_headers_and_limits_middleware(request: Request, call_next):
        # 1. Enforce HTTPS redirect if reverse proxy received plain HTTP
        if request.headers.get("x-forwarded-proto") == "http":
            url = request.url.replace(scheme="https")
            return Response(status_code=307, headers={"Location": str(url)})

        # 2. Enforce payload size limit (reject payloads > 10MB to mitigate DoS)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 10 * 1024 * 1024:
                    return JSONResponse(status_code=413, content={"detail": "Payload exceeds maximum allowed size (10MB)."})
            except ValueError:
                pass

        response = await call_next(request)

        # 3. Strict Defense-in-Depth Security Headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        
        is_tls = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        if is_tls:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: https: blob:; "
            "media-src 'self' blob: data:; "
            "connect-src 'self' ws: wss: https://api.dexscreener.com; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self';"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["Server"] = "SuperBan-Shield/3.2"
        return response

    @app.middleware("http")
    async def owner_guard_middleware(request: Request, call_next):
        path = request.url.path

        # Unprotected static routes, owner authentication, and uptime health probes
        public_paths = (
            "/", "/health", "/ping", "/favicon.ico", 
            "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
            "/manifest.json", "/site.webmanifest", "/icon-192.png", "/icon-512.png", "/icon.svg"
        )
        if path in public_paths or path.startswith("/static") or path.startswith("/api/owner/"):
            return await call_next(request)

        # Allow HEAD strictly for audio stream probing
        if request.method == "HEAD" and path == "/api/vc/audio_stream":
            return await call_next(request)

        token = (
            request.query_params.get("token")
            or request.headers.get("X-Owner-Token")
            or request.cookies.get("owner_token")
        )
        now = time.time()
        session = OPERATOR_SESSIONS.get(token)
        is_authenticated = False
        user_role = "operator"

        if session and session.get("expires", 0) >= now:
            is_authenticated = True
            user_role = session.get("role", "operator")
            request.state.operator = session
        elif token and token in OWNER_TOKENS and OWNER_TOKENS[token] >= now:
            is_authenticated = True
            user_role = "owner"
            request.state.operator = {"username": Config.get_owner_username(), "role": "owner"}

        if not is_authenticated:
            return JSONResponse(
                status_code=401,
                content={"detail": "Owner clearance required. Access denied."},
                headers={"X-Owner-Required": "true"}
            )

        # Strict Role-Based Access Control: Routes requiring Master Owner clearance
        owner_exclusive_prefixes = (
            "/api/operators",
            "/api/auth/send_code",
            "/api/auth/verify_code",
            "/api/auth/import_session",
            "/api/terminal/launch",
        )
        if any(path.startswith(prefix) for prefix in owner_exclusive_prefixes):
            if user_role != "owner":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden: Master Owner (@archfiend000) authorization required."}
                )

        # Operational command throttling (protect Telethon from FloodWait)
        operational_paths = (
            "/api/scan", "/api/execute", "/api/leads/scrape",
            "/api/copilot/execute", "/api/broadcast", "/api/service_messages/clean"
        )
        if any(path.startswith(op) for op in operational_paths):
            client_ip = get_client_ip(request)
            now_t = time.time()
            req_history = OPERATIONAL_RATE_LIMITS.setdefault(client_ip, [])
            OPERATIONAL_RATE_LIMITS[client_ip] = [t for t in req_history if t > now_t - 60]
            if len(OPERATIONAL_RATE_LIMITS[client_ip]) >= 30:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests: Operational command rate limit exceeded. Please wait a minute."},
                    headers={"Retry-After": "60"}
                )
            OPERATIONAL_RATE_LIMITS[client_ip].append(now_t)

        return await call_next(request)

    class OwnerPinReq(BaseModel):
        username: Optional[str] = None
        current_pin: Optional[str] = None
        new_pin: Optional[str] = None
        pin: Optional[str] = None

    class OperatorAssignReq(BaseModel):
        username: str
        pin: str

    class OperatorRevokeReq(BaseModel):
        username: str

    @app.get("/api/owner/status")
    async def get_owner_status(request: Request):
        token = request.headers.get("X-Owner-Token") or request.cookies.get("owner_token")
        now = time.time()
        session = OPERATOR_SESSIONS.get(token)
        is_authenticated = False
        username = None
        role = None

        if session and session.get("expires", 0) >= now:
            is_authenticated = True
            username = session.get("username")
            role = session.get("role")
        elif token and token in OWNER_TOKENS and OWNER_TOKENS[token] >= now:
            is_authenticated = True
            username = Config.get_owner_username()
            role = "owner"

        return {
            "authenticated": is_authenticated,
            "username": username,
            "role": role,
            "is_owner": (role == "owner"),
            "owner_username": Config.get_owner_username(),
            "pin_configured": True
        }

    @app.post("/api/owner/setup")
    async def setup_owner_pin(req: OwnerPinReq, request: Request, response: Response):
        client_ip = get_client_ip(request)
        now = time.time()
        record = FAILED_VERIFY_ATTEMPTS.get(client_ip, {"count": 0, "lockout_until": 0.0, "last_attempt": 0.0})

        if record["lockout_until"] > now:
            retry_after = max(1, int(record["lockout_until"] - now))
            raise HTTPException(
                status_code=429,
                detail=f"Security lockout active. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)}
            )

        clean_user = (req.username or Config.get_owner_username()).strip().lstrip('@').lower()
        target_new_pin = (req.new_pin or req.pin or "").strip()

        # Check if caller already has active authenticated owner session
        token = request.headers.get("X-Owner-Token") or request.cookies.get("owner_token")
        is_already_owner = bool(
            token and (
                (token in OPERATOR_SESSIONS and OPERATOR_SESSIONS[token].get("role") == "owner" and OPERATOR_SESSIONS[token].get("expires", 0) >= now) or
                (token in OWNER_TOKENS and OWNER_TOKENS[token] >= now)
            )
        )

        # If not already an authenticated owner, caller MUST provide and verify current_pin
        if not is_already_owner:
            provided_current = (req.current_pin or "").strip()
            if not provided_current or not Config.verify_owner_credentials(clean_user, provided_current):
                await asyncio.sleep(0.5)
                record["count"] += 1
                record["last_attempt"] = now
                if record["count"] >= 5:
                    record["lockout_until"] = now + 900
                    FAILED_VERIFY_ATTEMPTS[client_ip] = record
                    raise HTTPException(
                        status_code=429,
                        detail="Too many failed clearance attempts. Account locked for 15 minutes.",
                        headers={"Retry-After": "900"}
                    )
                FAILED_VERIFY_ATTEMPTS[client_ip] = record
                remaining = 5 - record["count"]
                raise HTTPException(
                    status_code=403,
                    detail=f"Current Master PIN verification failed. Setup denied. ({remaining} attempts remaining before temporary lockout)"
                )

        if len(target_new_pin) < 4:
            raise HTTPException(status_code=400, detail="Owner PIN must be at least 4 characters.")

        Config.save_owner_pin(target_new_pin)
        FAILED_VERIFY_ATTEMPTS[client_ip] = {"count": 0, "lockout_until": 0.0, "last_attempt": now}

        new_token = secrets.token_hex(32)
        expiry = now + (7 * 86400)
        OWNER_TOKENS[new_token] = expiry
        OPERATOR_SESSIONS[new_token] = {
            "username": clean_user,
            "role": "owner",
            "expires": expiry
        }
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            key="owner_token",
            value=new_token,
            max_age=7 * 86400,
            httponly=True,
            secure=is_secure,
            samesite="lax"
        )
        return {"status": "success", "token": new_token, "username": clean_user, "role": "owner"}

    @app.post("/api/owner/verify")
    async def verify_owner_pin(req: OwnerPinReq, request: Request, response: Response):
        client_ip = get_client_ip(request)
        now = time.time()
        record = FAILED_VERIFY_ATTEMPTS.get(client_ip, {"count": 0, "lockout_until": 0.0, "last_attempt": 0.0})

        # Check if currently locked out
        if record["lockout_until"] > now:
            retry_after = max(1, int(record["lockout_until"] - now))
            raise HTTPException(
                status_code=429,
                detail=f"Security lockout active due to excessive failed attempts. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)}
            )

        clean_user = (req.username or Config.get_owner_username()).strip().lstrip('@').lower()
        candidate_pin = req.pin.strip()
        auth_success = False
        role = "operator"

        if clean_user == Config.get_owner_username():
            # Master Owner verification (@archfiend000 with 1704 or stored PIN)
            if Config.verify_owner_credentials(clean_user, candidate_pin):
                auth_success = True
                role = "owner"
        elif engine.db:
            # Check registered operator clearance in SQLite
            op_record = await engine.db.get_operator_clearance(clean_user)
            if op_record and op_record.get("status") == "active":
                if Config.verify_pin(candidate_pin, op_record["pin_hash"]):
                    auth_success = True
                    role = op_record.get("role", "operator")

        if not auth_success:
            await asyncio.sleep(0.5)
            record["count"] += 1
            record["last_attempt"] = now
            if record["count"] >= 5:
                record["lockout_until"] = now + 900  # 15-minute lockout
                FAILED_VERIFY_ATTEMPTS[client_ip] = record
                raise HTTPException(
                    status_code=429,
                    detail="Too many failed clearance attempts. Account locked for 15 minutes.",
                    headers={"Retry-After": "900"}
                )
            FAILED_VERIFY_ATTEMPTS[client_ip] = record
            remaining = 5 - record["count"]
            raise HTTPException(
                status_code=403,
                detail=f"Invalid Owner PIN. Clearance Denied. ({remaining} attempts remaining before temporary lockout)"
            )

        # Successful verification: reset failed attempt counter
        FAILED_VERIFY_ATTEMPTS[client_ip] = {"count": 0, "lockout_until": 0.0, "last_attempt": now}

        # Auto-upgrade legacy plaintext PIN to PBKDF2 hash on successful login
        current_pin = Config.get_owner_pin()
        if role == "owner" and current_pin and not current_pin.startswith("pbkdf2_sha256$"):
            Config.save_owner_pin(candidate_pin)

        token = secrets.token_hex(32)
        expiry = now + (7 * 86400)
        OPERATOR_SESSIONS[token] = {
            "username": clean_user,
            "role": role,
            "expires": expiry
        }
        OWNER_TOKENS[token] = expiry
        
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            key="owner_token",
            value=token,
            max_age=7 * 86400,
            httponly=True,
            secure=is_secure,
            samesite="lax"
        )
        return {"status": "authorized", "token": token, "username": clean_user, "role": role, "is_owner": (role == "owner")}

    @app.post("/api/owner/logout")
    async def logout_owner(request: Request, response: Response):
        token = request.headers.get("X-Owner-Token") or request.cookies.get("owner_token")
        if token:
            if token in OPERATOR_SESSIONS:
                del OPERATOR_SESSIONS[token]
            if token in OWNER_TOKENS:
                del OWNER_TOKENS[token]
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.delete_cookie(key="owner_token", httponly=True, secure=is_secure, samesite="lax")
        return {"status": "logged_out"}

    # ═══════════════════════════════════════════════════════
    #   OPERATOR CLEARANCE MANAGEMENT (OWNER-ONLY)
    # ═══════════════════════════════════════════════════════
    @app.get("/api/operators")
    async def list_operators(request: Request):
        if not engine.db:
            return []
        records = await engine.db.list_operator_clearances()
        # Always guarantee master owner @archfiend000 is present
        has_owner = any(r.get("username") == "archfiend000" for r in records)
        if not has_owner:
            records.insert(0, {
                "id": 0,
                "username": "archfiend000",
                "role": "owner",
                "status": "active",
                "assigned_by": "system",
                "created_at": "Genesis",
                "updated_at": "Active"
            })
        return records

    @app.post("/api/operators/assign")
    async def assign_operator(req: OperatorAssignReq, request: Request):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database persistence layer unavailable")

        clean_user = req.username.strip().lstrip('@').lower()
        pin = req.pin.strip()

        if not clean_user:
            raise HTTPException(status_code=400, detail="Telegram username cannot be empty.")
        if clean_user == "archfiend000":
            raise HTTPException(status_code=400, detail="Master Owner (@archfiend000) credentials cannot be overwritten via this endpoint.")
        if len(pin) != 4 or not pin.isdigit():
            raise HTTPException(status_code=400, detail="Assigned clearance code must be exactly 4 digits (0000-9999).")

        pin_hash = Config.hash_pin(pin)
        saved = await engine.db.save_operator_clearance(clean_user, pin_hash, role="operator", assigned_by="archfiend000")

        # Automatically dispatch assigned credentials to @archfiend000 Telegram DM
        dm_dispatched = await engine.notify_owner_clearance_issued(clean_user, pin, role="operator")

        return {
            "status": "success",
            "username": clean_user,
            "pin": pin,
            "role": "operator",
            "dm_dispatched": dm_dispatched,
            "message": f"Clearance granted to @{clean_user}. Assigned code dispatched to @archfiend000 Telegram DM."
        }

    @app.post("/api/operators/revoke")
    async def revoke_operator(req: OperatorRevokeReq, request: Request):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database persistence layer unavailable")

        clean_user = req.username.strip().lstrip('@').lower()
        if clean_user == "archfiend000":
            raise HTTPException(status_code=400, detail="Cannot revoke Master Owner (@archfiend000).")

        success = await engine.db.revoke_operator_clearance(clean_user)
        if not success:
            raise HTTPException(status_code=404, detail="Operator not found or already revoked.")

        # Invalidate active operator sessions
        tokens_to_remove = [t for t, s in OPERATOR_SESSIONS.items() if s.get("username") == clean_user]
        for t in tokens_to_remove:
            del OPERATOR_SESSIONS[t]
            if t in OWNER_TOKENS:
                del OWNER_TOKENS[t]

        dm_dispatched = await engine.notify_owner_clearance_revoked(clean_user)

        return {
            "status": "success",
            "username": clean_user,
            "dm_dispatched": dm_dispatched,
            "message": f"Operator @{clean_user} clearance revoked and sessions terminated."
        }

    @app.delete("/api/operators")
    async def delete_operator(username: str, request: Request):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database persistence layer unavailable")

        clean_user = username.strip().lstrip('@').lower()
        if clean_user == "archfiend000":
            raise HTTPException(status_code=400, detail="Cannot delete Master Owner.")

        await engine.db.delete_operator_clearance(clean_user)
        tokens_to_remove = [t for t, s in OPERATOR_SESSIONS.items() if s.get("username") == clean_user]
        for t in tokens_to_remove:
            del OPERATOR_SESSIONS[t]
            if t in OWNER_TOKENS:
                del OWNER_TOKENS[t]

        return {"status": "success", "username": clean_user}

    @app.get("/", response_class=HTMLResponse)
    @app.head("/")
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
    #   PWA & BRAND ASSET SERVING (ICONS, MANIFEST, FAVICONS)
    # ═══════════════════════════════════════════════════════
    @app.get("/manifest.json")
    @app.get("/site.webmanifest")
    async def get_manifest():
        f = STATIC_DIR / "manifest.json"
        if not f.exists():
            raise HTTPException(status_code=404, detail="Manifest not found")
        return FileResponse(f, media_type="application/manifest+json")

    @app.get("/favicon.ico")
    async def get_favicon():
        f = STATIC_DIR / "favicon.ico"
        if not f.exists():
            raise HTTPException(status_code=404, detail="Favicon not found")
        return FileResponse(f, media_type="image/x-icon")

    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    async def get_apple_touch_icon():
        f = STATIC_DIR / "apple-touch-icon.png"
        if not f.exists():
            raise HTTPException(status_code=404, detail="Icon not found")
        return FileResponse(f, media_type="image/png")

    @app.get("/icon-192.png")
    async def get_icon_192():
        f = STATIC_DIR / "icon-192.png"
        if not f.exists():
            raise HTTPException(status_code=404, detail="Icon not found")
        return FileResponse(f, media_type="image/png")

    @app.get("/icon-512.png")
    async def get_icon_512():
        f = STATIC_DIR / "icon-512.png"
        if not f.exists():
            raise HTTPException(status_code=404, detail="Icon not found")
        return FileResponse(f, media_type="image/png")

    @app.get("/icon.svg")
    async def get_icon_svg():
        f = STATIC_DIR / "icon.svg"
        if not f.exists():
            raise HTTPException(status_code=404, detail="Icon not found")
        return FileResponse(f, media_type="image/svg+xml")

    # Dedicated Keep-Alive & Health Check Probes for UptimeRobot / Cloud Monitoring
    @app.get("/health")
    @app.head("/health")
    @app.get("/ping")
    @app.head("/ping")
    async def health_probe():
        return Response(content="OK", media_type="text/plain", status_code=200)

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
                "session_saved": bool(saved_str),
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

    class ImportSessionReq(BaseModel):
        session_string: str

    @app.post("/api/auth/import_session")
    async def import_session(req: ImportSessionReq):
        session_str = req.session_string.strip()
        if not session_str:
            raise HTTPException(status_code=400, detail="Session string cannot be empty")
        creds = Config.get_credentials()
        if not creds:
            raise HTTPException(status_code=400, detail="Missing Telegram credentials in .env")
        try:
            test_client = TelegramClient(StringSession(session_str), creds["api_id"], creds["api_hash"])
            await test_client.connect()
            if not await test_client.is_user_authorized():
                await test_client.disconnect()
                raise HTTPException(status_code=400, detail="The provided session string is invalid or expired.")

            me = await test_client.get_me()
            await test_client.disconnect()

            Config.save_string_session(session_str)

            try:
                await engine.client.disconnect()
            except Exception:
                pass
            engine.client = TelegramClient(StringSession(session_str), creds["api_id"], creds["api_hash"])
            await engine.client.connect()
            await engine.get_dialogs(force=True)

            return {
                "status": "authorized",
                "session_saved": True,
                "user": {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username
                }
            }
        except Exception as e:
            logger.error(f"Error importing session string: {e}")
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
        try:
            if not engine.client.is_connected():
                await engine.client.connect()
        except Exception as e:
            logger.warning(f"Engine client auto-connect in get_dialogs: {e}")

        try:
            is_auth = await engine.client.is_user_authorized()
        except Exception as e:
            logger.warning(f"is_user_authorized check failed: {e}")
            is_auth = False

        if not is_auth:
            return JSONResponse(
                status_code=200,
                content={
                    "authorized": False,
                    "error": "Telegram account not authorized",
                    "groups": []
                }
            )

        try:
            groups = await engine.get_dialogs()
            group_list = [{"id": g["id"], "title": g["title"], "members_count": g["members_count"]} for g in groups]
            return JSONResponse(
                status_code=200,
                content={
                    "authorized": True,
                    "groups": group_list
                }
            )
        except Exception as e:
            logger.error(f"Error fetching dialogs: {e}")
            return JSONResponse(
                status_code=200,
                content={
                    "authorized": True,
                    "error": str(e),
                    "groups": []
                }
            )

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

        if "brief" in prompt or "intel" in prompt or "summary" in prompt:
            return {"action": "switch_tab", "tab": "intel", "reply": "🧠 Tactical AI Briefing activated. Synthesizing group narrative, sentiment, and whale drivers..."}
        elif "scan" in prompt or "member" in prompt or "recon" in prompt:
            return {"action": "switch_tab", "tab": "recon", "reply": "🔍 Deep Member Reconnaissance activated. Profiling group participants with Trust Scores..."}
        elif "alpha" in prompt or "token" in prompt or "contract" in prompt or "crypto" in prompt:
            return {"action": "switch_tab", "tab": "alpha", "reply": "📈 Alpha & DexScreener Radar activated. Inspecting intercepted contract addresses and liquidity..."}
        elif "ghost" in prompt or "delete" in prompt:
            return {"action": "switch_tab", "tab": "ghost", "reply": "👻 Ghost Vault activated. Revealing all deleted and edited messages with audit diffs..."}
        elif "siphon" in prompt or "ping" in prompt or "phone" in prompt:
            test_res = await engine.test_siphon_ping()
            return {"action": "siphon_tested", "reply": f"🛸 Siphon dispatch sent directly to your Telegram Saved Messages (Phone): {test_res.get('status', 'delivered')}"}
        elif "lockdown" in prompt or "mute all" in prompt:
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
        in_call = engine.is_in_voice_call(group_id)
        return Response(
            status_code=200 if (in_call or os.path.exists(audio_path)) else 404,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Accept-Ranges": "none",
                "Access-Control-Allow-Origin": "*",
                "X-Content-Type-Options": "nosniff"
            }
        )

    @app.get("/api/vc/audio_stream")
    async def vc_audio_stream(group_id: int):
        clean_gid = str(group_id).replace("-100", "").replace("-", "")
        audio_path = f"/tmp/vc_stream_{clean_gid}.mp3"

        # Check if voice chat session is registered or in flight
        is_active = engine.is_in_voice_call(group_id)
        if not is_active:
            for _ in range(30):
                if engine.is_in_voice_call(group_id) or os.path.exists(audio_path):
                    is_active = True
                    break
                await asyncio.sleep(0.1)

        if not is_active and not os.path.exists(audio_path):
            raise HTTPException(status_code=404, detail="No active voice chat audio stream found on server")

        async def stream_generator():
            silence_block = get_mp3_silence_chunk()
            f = None
            try:
                # 1. Connect to audio buffer file with MP3 frame alignment
                file_opened = False
                for _ in range(50):
                    if os.path.exists(audio_path) and os.path.getsize(audio_path) > 1024:
                        try:
                            f = open(audio_path, "rb")
                            cur_sz = os.path.getsize(audio_path)
                            if cur_sz > 98304:
                                # High history: give 4 seconds of buffer cushion (~64KB)
                                target = cur_sz - 65536
                                seek_pos = find_mp3_sync_offset(audio_path, target)
                                f.seek(seek_pos)
                            elif cur_sz > 32768:
                                # Medium history: seek back ~2 seconds (~32KB)
                                target = cur_sz - 32768
                                seek_pos = find_mp3_sync_offset(audio_path, target)
                                f.seek(seek_pos)
                            else:
                                f.seek(0)
                            file_opened = True
                            break
                        except Exception as open_err:
                            logger.debug(f"Stream open note: {open_err}")
                    # If file is not yet flushed to disk, stream initial comfort silence to prime the client player
                    if silence_block:
                        yield silence_block
                    await asyncio.sleep(0.1)

                if not file_opened and os.path.exists(audio_path):
                    try:
                        f = open(audio_path, "rb")
                        file_opened = True
                    except Exception:
                        pass

                # 2. Main Live Audio Streaming Loop
                last_speech_time = time.time()
                while engine.is_in_voice_call(group_id):
                    if not file_opened and os.path.exists(audio_path) and os.path.getsize(audio_path) > 1024:
                        try:
                            f = open(audio_path, "rb")
                            file_opened = True
                        except Exception:
                            pass

                    chunk = None
                    if f:
                        try:
                            chunk = f.read(8192)
                        except Exception:
                            chunk = None

                    if chunk:
                        last_speech_time = time.time()
                        yield chunk
                        await asyncio.sleep(0.01)
                    else:
                        idle_elapsed = time.time() - last_speech_time
                        if idle_elapsed > 0.35 and silence_block:
                            # Keep pipe primed during voice chat DTX silence intervals
                            yield silence_block
                            last_speech_time = time.time() - 0.12
                            await asyncio.sleep(0.04)
                        else:
                            await asyncio.sleep(0.015)

                if f:
                    try:
                        leftover = f.read()
                        if leftover:
                            yield leftover
                    except Exception:
                        pass
            except (asyncio.CancelledError, GeneratorExit):
                pass
            except Exception as e:
                logger.debug(f"Audio stream generator note: {e}")
            finally:
                if f:
                    try:
                        f.close()
                    except Exception:
                        pass

        return StreamingResponse(
            stream_generator(),
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Accept-Ranges": "none",
                "X-Content-Type-Options": "nosniff",
                "Access-Control-Allow-Origin": "*",
                "Connection": "keep-alive"
            }
        )

    class VCLeaveReq(BaseModel):
        group_id: Optional[int] = None
        auto_send_saved: bool = False

    @app.post("/api/vc/leave")
    async def leave_vc(req: VCLeaveReq):
        try:
            return await engine.leave_voice_call(group_entity=req.group_id, auto_send_saved=req.auto_send_saved)
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
        token = (
            websocket.query_params.get("token")
            or websocket.cookies.get("owner_token")
            or websocket.headers.get("X-Owner-Token")
        )
        if not token or token not in OWNER_TOKENS or OWNER_TOKENS[token] < time.time():
            await websocket.close(code=1008, reason="Owner clearance required")
            return

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
    class MessageReactReq(BaseModel):
        group_id: int
        message_id: int
        emoji: str = "👍"

    @app.post("/api/message/react")
    async def react_to_message(req: MessageReactReq):
        try:
            return await engine.react_to_message(req.group_id, req.message_id, req.emoji)
        except ValueError as ve:
            raise HTTPException(status_code=404, detail=str(ve))
        except Exception as e:
            logger.error(f"Failed to react to message: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    class GhostForwardReq(BaseModel):
        ghost_id: int

    @app.post("/api/ghost/forward_to_saved")
    async def forward_ghost_to_saved(req: GhostForwardReq):
        try:
            return await engine.forward_ghost_evidence_to_saved(req.ghost_id)
        except ValueError as ve:
            raise HTTPException(status_code=404, detail=str(ve))
        except Exception as e:
            logger.error(f"Failed to forward ghost evidence: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    class ThreatWarnReq(BaseModel):
        group_id: int
        message_id: int
        threat_type: str = "PHISHING"
        custom_warning: Optional[str] = None

    @app.post("/api/threat/warn")
    async def dispatch_threat_warning(req: ThreatWarnReq):
        try:
            return await engine.send_counter_threat_warning(
                req.group_id, req.message_id, req.threat_type, req.custom_warning
            )
        except ValueError as ve:
            raise HTTPException(status_code=404, detail=str(ve))
        except Exception as e:
            logger.error(f"Failed to dispatch threat warning: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    class ThreatReportReq(BaseModel):
        group_id: int
        message_id: int
        reason: str = "spam"
        details: Optional[str] = None

    @app.post("/api/threat/report")
    async def report_threat_endpoint(req: ThreatReportReq):
        """Plan H: 1-Click MTProto Report directly to Telegram Trust & Safety."""
        try:
            return await engine.report_threat_to_telegram(
                req.group_id, req.message_id, req.reason, req.details or ""
            )
        except Exception as e:
            logger.error(f"Failed to dispatch threat report to Telegram T&S: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    class StealthBroadcastReq(BaseModel):
        group_ids: List[int]
        text: str
        typing_delay: float = 3.0

    @app.post("/api/broadcast/stealth")
    async def stealth_broadcast(req: StealthBroadcastReq):
        try:
            return await engine.schedule_stealth_broadcast(
                req.group_ids, req.text, req.typing_delay
            )
        except Exception as e:
            logger.error(f"Failed stealth broadcast: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/ghost/messages")
    async def get_ghost_messages(group_id: Optional[int] = None, filter_mode: str = 'all', limit: int = 50):
        if not engine.db:
            return []
        return await engine.db.get_ghost_messages(group_id=group_id, filter_mode=filter_mode, limit=limit)

    @app.get("/api/ghost/media/{ghost_id}")
    async def get_ghost_media(ghost_id: int):
        """Plan B: Ephemeral Document & Ghost Media Vault secure retrieval."""
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        record = await engine.db.get_ghost_message_by_id(ghost_id)
        if not record or not record.get("media_path"):
            raise HTTPException(status_code=404, detail="Ghost media record or file not found")
        
        file_path = Path(record["media_path"]).resolve()
        vault_root = MEDIA_VAULT_DIR.resolve()
        
        # Path traversal guard
        try:
            file_path.relative_to(vault_root)
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied: Invalid media path")

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Media file was not preserved on disk")

        return FileResponse(
            str(file_path),
            filename=record.get("file_name") or file_path.name,
            headers={"X-Content-Type-Options": "nosniff"}
        )

    @app.get("/api/vc/replay")
    async def get_vc_replay(group_id: int, seconds: int = 30):
        slice_path = await engine.get_replay_audio_slice(group_id, seconds=seconds)
        if not slice_path or not os.path.exists(slice_path):
            raise HTTPException(status_code=404, detail="No active replay buffer available for this group")
        return FileResponse(slice_path, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})

    class VCSendSavedReq(BaseModel):
        group_id: int
        seconds: Optional[int] = None
        as_voice_note: bool = False

    @app.post("/api/vc/send_to_saved")
    async def send_vc_to_saved(req: VCSendSavedReq):
        try:
            return await engine.send_vc_recording_to_saved(
                group_id=req.group_id,
                seconds=req.seconds,
                as_voice_note=req.as_voice_note
            )
        except ValueError as ve:
            raise HTTPException(status_code=404, detail=str(ve))
        except Exception as e:
            logger.error(f"Failed to send VC recording to Saved Messages: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/vc/download_recording")
    async def download_vc_recording(group_id: int):
        clean_gid = str(group_id).replace("-100", "").replace("-", "")
        live_path = f"/tmp/vc_stream_{clean_gid}.mp3"
        last_path = f"/tmp/vc_last_session_{clean_gid}.mp3"

        target_path = live_path if (os.path.exists(live_path) and os.path.getsize(live_path) > 1024) else last_path
        if not os.path.exists(target_path) or os.path.getsize(target_path) < 512:
            raise HTTPException(status_code=404, detail="No voice chat recording archive found for this group")
        return FileResponse(
            target_path,
            media_type="audio/mpeg",
            filename=f"VC_Recording_{clean_gid}_{int(time.time())}.mp3"
        )

    @app.get("/api/vc/leaderboard")
    async def get_vc_leaderboard(group_id: int, call_id: Optional[str] = None):
        if not engine.db:
            return []
        return await engine.db.get_vc_talker_leaderboard(group_id, call_id=call_id)

    class VCTranscribeReq(BaseModel):
        group_id: int
        audio_path: Optional[str] = None

    @app.post("/api/vc/transcribe")
    async def transcribe_vc_endpoint(req: VCTranscribeReq):
        """Plan A: Voice Chat Speech-to-Text & AI Summarizer (dispatched to @archfiend000 DM)."""
        try:
            return await engine.transcribe_and_summarize_vc(req.group_id, req.audio_path)
        except Exception as e:
            logger.error(f"Failed to transcribe VC: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/osint/user")
    async def get_user_osint(user_id: int):
        u = None
        try:
            u = await engine.client.get_entity(user_id)
        except Exception:
            pass

        full_name = f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip()
        username = getattr(u, 'username', '') or ''

        forensic_info = ForensicInspector.inspect_user(u) if u else {
            "trust_score": 70,
            "trust_level": "NEUTRAL CITIZEN",
            "vintage": "Standard Account",
            "risk_flags": []
        }

        mutual_chats = []
        try:
            if u:
                input_user = await engine.client.get_input_entity(u)
                res = await engine.client(GetCommonChatsRequest(user_id=input_user, max_id=0, limit=100))
                for c in getattr(res, 'chats', []):
                    mutual_chats.append({
                        "id": c.id,
                        "title": getattr(c, 'title', '') or f"Chat #{c.id}",
                        "username": getattr(c, 'username', '') or '',
                        "participants_count": getattr(c, 'participants_count', 0)
                    })
        except Exception as e:
            logger.debug(f"Mutual chats lookup error: {e}")

        db_profile = {}
        if engine.db:
            db_profile = await engine.db.get_user_cross_profile(user_id) or {}

        if not u and not db_profile:
            raise HTTPException(status_code=404, detail="Target user profile not found in radar")

        return {
            "user_id": user_id,
            "current_name": full_name or db_profile.get("current_name") or f"User #{user_id}",
            "current_username": username or db_profile.get("current_username") or "",
            "phone": getattr(u, 'phone', '') or db_profile.get("phone", ""),
            "is_premium": bool(getattr(u, 'premium', False)),
            "is_verified": bool(getattr(u, 'verified', False)),
            "is_bot": bool(getattr(u, 'bot', False)),
            "is_scam": bool(getattr(u, 'scam', False)),
            "is_fake": bool(getattr(u, 'fake', False)),
            "trust_score": forensic_info.get("trust_score", 70),
            "trust_level": forensic_info.get("trust_level", "NEUTRAL CITIZEN"),
            "vintage": forensic_info.get("vintage", "Standard Account"),
            "risk_flags": forensic_info.get("risk_flags", []),
            "mutual_chats": mutual_chats,
            "mutual_chats_count": len(mutual_chats),
            "spotted_groups": db_profile.get("spotted_groups", []),
            "name_history": db_profile.get("name_history", [full_name] if full_name else []),
            "username_history": db_profile.get("username_history", [username] if username else []),
            "message_count": db_profile.get("message_count", 0),
            "vc_participations": db_profile.get("vc_participations", 0),
            "first_seen": db_profile.get("first_seen", "Live MTProto Radar"),
            "last_seen": db_profile.get("last_seen", "Active")
        }

    @app.get("/api/osint/syndicates")
    async def get_syndicates(time_window_sec: int = 120, min_cooccurrences: int = 2):
        """Plan C: Co-occurrence Syndicate & Shill Ring detection across monitored chats."""
        if not engine.db:
            return []
        return await engine.db.detect_syndicate_rings(time_window_sec=time_window_sec, min_cooccurrences=min_cooccurrences)

    @app.get("/api/osint/user/{user_id}/sleep_cycle")
    async def get_user_sleep_cycle(user_id: int):
        """Plan C: Active timezone and 24-hour circadian sleep-cycle profiler."""
        if not engine.db:
            return {"error": "Database offline"}
        return await engine.db.get_user_sleep_cycle_profile(user_id)

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
    #  AI & HEURISTIC CHAT INTEL SYNTHESIZER
    # =========================================================================
    @app.get("/api/intel/briefing")
    async def get_chat_intel_briefing(group_id: int, limit: int = 100):
        entity = await engine.get_group_entity(group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        group_title = getattr(entity, 'title', '') or ''
        messages = await engine.get_recent_chat_messages(entity, limit=limit)
        return IntelSynthesizer.synthesize_chat_intel(messages, group_title=group_title)

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

    class SiphonForwardReq(BaseModel):
        group_id: int
        message_id: int

    @app.post("/api/siphon/forward")
    async def forward_to_saved(req: SiphonForwardReq):
        res = await engine.siphon_message_to_me(req.group_id, req.message_id)
        if res.get("status") == "error":
            raise HTTPException(status_code=400, detail=res.get("error", "Siphon delivery failed"))
        return res

    @app.post("/api/siphon/test")
    async def test_siphon():
        return await engine.test_siphon_ping()

    @app.get("/api/crypto/dexscreener")
    async def get_dexscreener_info(address: str):
        import re
        cleaned_addr = re.sub(r"[^a-zA-Z0-9_-]", "", address.strip())
        if not cleaned_addr or len(cleaned_addr) > 128:
            return {
                "found": False,
                "address": address,
                "url": "https://dexscreener.com"
            }
        url = f"https://api.dexscreener.com/latest/dex/tokens/{cleaned_addr}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs") or []
                        if pairs:
                            pairs.sort(key=lambda p: float(p.get("liquidity", {}).get("usd") or 0), reverse=True)
                            p = pairs[0]
                            return {
                                "found": True,
                                "name": p.get("baseToken", {}).get("name", "Unknown"),
                                "symbol": p.get("baseToken", {}).get("symbol", "???"),
                                "chain": p.get("chainId", ""),
                                "dex": p.get("dexId", ""),
                                "price_usd": p.get("priceUsd", "0.00"),
                                "liquidity_usd": p.get("liquidity", {}).get("usd", 0),
                                "fdv": p.get("fdv", 0),
                                "price_change_24h": p.get("priceChange", {}).get("h24", 0),
                                "url": p.get("url", f"https://dexscreener.com/search?q={cleaned_addr}")
                            }
        except Exception as e:
            logger.debug(f"DexScreener proxy error: {e}")
        return {
            "found": False,
            "address": cleaned_addr,
            "url": f"https://dexscreener.com/search?q={cleaned_addr}"
        }

    @app.get("/api/export/members")
    async def export_members_csv(group_id: int, format: str = "csv"):
        entity = await engine.get_group_entity(group_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Target group not found")
        members = await engine.inspect_group_members(entity, limit=5000)
        
        if format == "json":
            return members
            
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Full Name", "Username", "Trust Score", "Trust Level", "Vintage", "Is Premium", "Is Verified", "Is Bot", "Is Scam", "Is Fake"])
        for m in members:
            writer.writerow([
                m.get("user_id"),
                m.get("name"),
                m.get("username"),
                m.get("trust_score", 70),
                m.get("trust_level", "NEUTRAL CITIZEN"),
                m.get("vintage", "Standard Account"),
                "YES" if m.get("is_premium") else "NO",
                "YES" if m.get("is_verified") else "NO",
                "YES" if m.get("is_bot") else "NO",
                "YES" if m.get("is_scam") else "NO",
                "YES" if m.get("is_fake") else "NO"
            ])
        output.seek(0)
        clean_title = "".join(c for c in getattr(entity, 'title', 'group') if c.isalnum() or c in ('_', '-')) or "group"
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=superban_members_{clean_title}.csv"}
        )

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
    async def launch_native_terminal(request: Request):
        op = getattr(request.state, "operator", {})
        if op.get("role") != "owner":
            raise HTTPException(status_code=403, detail="Forbidden: Master Owner (@archfiend000) authorization required.")
        import platform
        import subprocess
        if platform.system() != "Darwin":
            return {"status": "disabled", "message": "Native Terminal is only available in local macOS console mode."}
        try:
            cmd_path = Path(__file__).resolve().parent.parent / "launch_bot.command"
            if not cmd_path.exists():
                return {"status": "error", "message": "Console launch script not found."}
            subprocess.Popen(["open", str(cmd_path)])
            return {"status": "launched", "message": "Mac Terminal.app opened with Afterlight Console."}
        except Exception as e:
            logger.error(f"Failed to launch terminal: {e}")
            return {"status": "error", "message": str(e)}

    # =========================================================================
    #  SOVEREIGN FORENSIC DATA EXPORTERS (CSV / JSON)
    # =========================================================================
    @app.get("/api/export/entities")
    async def export_entities_csv(group_id: Optional[int] = None, format: str = "csv"):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        data = await engine.db.get_extracted_entities(group_id=group_id, limit=5000)
        items = data.get("items", [])
        
        if format == "json":
            return items
            
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Timestamp", "Group Title", "Group ID", "Sender ID", "Sender Name", "Entity Type", "Entity Value", "Context"])
        for it in items:
            writer.writerow([
                it.get("id"), it.get("timestamp"), it.get("group_title"), it.get("group_id"),
                it.get("sender_id"), it.get("sender_name"), it.get("entity_type"),
                it.get("entity_value"), it.get("context")
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=superban_crypto_signals_alpha.csv"}
        )

    @app.get("/api/export/ghost")
    async def export_ghost_csv(group_id: Optional[int] = None, format: str = "csv"):
        if not engine.db:
            raise HTTPException(status_code=500, detail="Database offline")
        items = await engine.db.get_ghost_messages(group_id=group_id, filter_mode='all', limit=5000)
        
        if format == "json":
            return items
            
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["ID", "Status", "Date Sent", "Date Deleted/Edited", "Group Title", "Sender ID", "Sender Name", "Username", "Message Text", "Original Text Before Edit"])
        for it in items:
            status = "DELETED" if it.get("is_deleted") else "EDITED"
            date_action = it.get("date_deleted") or it.get("date_edited") or ""
            writer.writerow([
                it.get("id"), status, it.get("date_sent"), date_action, it.get("group_title"),
                it.get("user_id"), it.get("full_name"), it.get("username"),
                it.get("text"), it.get("original_text") or ""
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=superban_ghost_vault_evidence.csv"}
        )

    # Mount /static for public assets (icons, manifest, etc.)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
