import asyncio
import json
import os
import sys
import secrets
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.config import Config
from web.app import create_app

class MockSentinel:
    def register_callback(self, cb):
        pass

class MockEngine:
    def __init__(self):
        self.sentinel = MockSentinel()
        self.telemetry = {
            "processed": 42,
            "remaining": 10,
            "successful": 40,
            "speed": 8.5,
            "state": "IDLE"
        }
        self.db = None
        self.client = None

    def register_listener(self, cb):
        pass

class AsgiResponse:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self):
        return self.body.decode("utf-8")

class InProcessAsgiClient:
    """Pure in-process ASGI 3.0 test client running without network sockets."""
    def __init__(self, app):
        self.app = app

    async def request(
        self,
        method: str,
        path: str,
        headers: dict = None,
        json_data: dict = None,
        body: bytes = b"",
        query_string: str = "",
        client_ip: str = "127.0.0.1"
    ) -> AsgiResponse:
        headers = headers or {}
        req_headers = {}
        for k, v in headers.items():
            req_headers[k.lower()] = v

        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
            req_headers["content-type"] = "application/json"
            req_headers["content-length"] = str(len(body))
        elif body and "content-length" not in req_headers:
            req_headers["content-length"] = str(len(body))

        if "host" not in req_headers:
            req_headers["host"] = "testserver"

        raw_headers = [
            (k.encode("latin1"), v.encode("latin1"))
            for k, v in req_headers.items()
        ]

        # Extract path and query if passed together
        if "?" in path and not query_string:
            path, query_string = path.split("?", 1)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": raw_headers,
            "client": (client_ip, 50000),
            "server": ("127.0.0.1", 80),
        }

        response_status = None
        response_headers = {}
        response_body = bytearray()
        body_sent = False

        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_status, response_headers, response_body
            if message["type"] == "http.response.start":
                response_status = message["status"]
                for k, v in message.get("headers", []):
                    key = k.decode("latin1").lower()
                    val = v.decode("latin1")
                    if key in response_headers:
                        response_headers[key] += f", {val}"
                    else:
                        response_headers[key] = val
            elif message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))

        await self.app(scope, receive, send)
        return AsgiResponse(response_status, response_headers, bytes(response_body))

    async def websocket_connect(self, path: str, query_string: str = "", headers: dict = None):
        """Simulate ASGI WebSocket connection handshake and return events."""
        headers = headers or {}
        raw_headers = [
            (k.lower().encode("latin1"), v.encode("latin1"))
            for k, v in headers.items()
        ]
        if "?" in path and not query_string:
            path, query_string = path.split("?", 1)

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "headers": raw_headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 80),
            "subprotocols": [],
        }

        events = []
        client_connected = False

        async def receive():
            if not client_connected:
                return {"type": "websocket.connect"}
            return {"type": "websocket.disconnect", "code": 1000}

        async def send(message):
            nonlocal client_connected
            events.append(message)
            if message["type"] == "websocket.accept":
                client_connected = True

        try:
            await self.app(scope, receive, send)
        except Exception as e:
            events.append({"type": "exception", "error": str(e)})

        return events


async def run_security_tests():
    print("=" * 70)
    print("🛡️  SUPERBAN OS // COMPREHENSIVE SECURITY VERIFICATION SUITE")
    print("=" * 70)

    # ---------------------------------------------------------
    # TEST 1: PBKDF2 Cryptographic PIN Hashing & Verification
    # ---------------------------------------------------------
    print("\n[TEST 1] PBKDF2 Cryptographic PIN Hashing & Verification...")
    pin = "481516"
    h1 = Config.hash_pin(pin)
    h2 = Config.hash_pin(pin)
    assert h1.startswith("pbkdf2_sha256$600000$"), f"Invalid PBKDF2 format: {h1}"
    assert h1 != h2, "Salts must be cryptographically unique per invocation"
    assert Config.verify_pin(pin, h1) is True, "Valid PIN must verify against PBKDF2 hash"
    assert Config.verify_pin("wrong", h1) is False, "Invalid PIN must fail verification"
    # Legacy plaintext check
    assert Config.verify_pin("legacy_secret", "legacy_secret") is True, "Legacy plaintext must verify"
    assert Config.verify_pin("legacy_secret", "bad_secret") is False, "Wrong plaintext must fail"
    print("  ✅ PASS: PBKDF2-HMAC-SHA256 (600,000 iter) verification & salt uniqueness confirmed.")

    # ---------------------------------------------------------
    # TEST 2: In-Process App Instantiation & HTTP Security Headers
    # ---------------------------------------------------------
    print("\n[TEST 2] HTTP Security Headers & Browser Hardening...")
    Config.save_owner_pin("1337")

    engine = MockEngine()
    app = create_app(engine)
    client = InProcessAsgiClient(app)

    res = await client.request("GET", "/health")
    assert res.status == 200, f"Expected 200, got {res.status}"
    headers = res.headers
    assert headers.get("x-content-type-options") == "nosniff", "Missing X-Content-Type-Options: nosniff"
    assert headers.get("x-frame-options") == "DENY", "Missing X-Frame-Options: DENY"
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin", "Missing Referrer-Policy"
    assert "camera=()" in headers.get("permissions-policy", ""), "Missing Permissions-Policy"
    csp = headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp, "CSP missing default-src"
    assert "frame-ancestors 'none'" in csp, "CSP missing frame-ancestors 'none'"
    assert "object-src 'none'" in csp, "CSP missing object-src 'none'"
    assert headers.get("server") == "SuperBan-Shield/3.2", "Server header not masked"
    print("  ✅ PASS: Security headers (X-Content-Type-Options, X-Frame-Options, CSP, Permissions-Policy) active.")

    # ---------------------------------------------------------
    # TEST 3: Unauthenticated REST API Access Denial
    # ---------------------------------------------------------
    print("\n[TEST 3] Unauthenticated Route Access Protection...")
    sensitive_routes = ["/api/status", "/api/messages", "/api/dialogs", "/api/ghost/messages", "/api/osint/user?user_id=123"]
    for route in sensitive_routes:
        res = await client.request("GET", route)
        assert res.status == 401, f"Route {route} expected 401 Unauthorized, got {res.status}"
        data = res.json()
        assert "Owner clearance required" in data.get("detail", "")
    print("  ✅ PASS: All sensitive REST endpoints strictly return 401 Unauthorized without owner clearance.")

    # ---------------------------------------------------------
    # TEST 4: Unauthenticated WebSocket Access Denial
    # ---------------------------------------------------------
    print("\n[TEST 4] Unauthenticated WebSocket Wiretap Denial...")
    events = await client.websocket_connect("/ws")
    # Verify that the server either closed with code 1008 or never sent accept
    accepted = any(e.get("type") == "websocket.accept" for e in events)
    closed = any(e.get("type") == "websocket.close" and e.get("code") == 1008 for e in events)
    assert not accepted, "WebSocket must NOT accept unauthenticated connections!"
    assert closed, f"WebSocket must close with code 1008 (Policy Violation). Events: {events}"
    print("  ✅ PASS: Unauthenticated WebSocket connection closed with code 1008 (Policy Violation).")

    # ---------------------------------------------------------
    # TEST 5: Brute-Force Rate Limiting & Lockout
    # ---------------------------------------------------------
    print("\n[TEST 5] Brute Force Rate Limiting & Lockout on /api/owner/verify...")
    test_ip = "203.0.113.199"
    # 4 failed attempts should yield 403
    for i in range(1, 5):
        res = await client.request(
            "POST",
            "/api/owner/verify",
            json_data={"pin": "0000"},
            headers={"x-forwarded-for": test_ip}
        )
        assert res.status == 403, f"Attempt {i} expected 403, got {res.status}"
        assert "attempts remaining" in res.json().get("detail", "")

    # 5th attempt must trigger 429 Too Many Requests
    res_5th = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"pin": "0000"},
        headers={"x-forwarded-for": test_ip}
    )
    assert res_5th.status == 429, f"5th attempt expected 429 Too Many Requests, got {res_5th.status}"
    assert "locked for 15 minutes" in res_5th.json().get("detail", "")
    assert "retry-after" in res_5th.headers, "Missing Retry-After header on 429 response"

    # Even a correct PIN from a locked IP must be blocked
    res_locked = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"pin": "1337"},
        headers={"x-forwarded-for": test_ip}
    )
    assert res_locked.status == 429, f"Locked IP expected 429, got {res_locked.status}"
    print("  ✅ PASS: Brute-force rate limiting triggered: IP locked out after 5 failures with 429 & Retry-After.")

    # ---------------------------------------------------------
    # TEST 6: Valid Authentication, Cookie Flags & Authorized WS
    # ---------------------------------------------------------
    print("\n[TEST 6] Legitimate Authentication & Cookie Security Flags...")
    clean_ip = "198.51.100.77"
    res_auth = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"pin": "1337"},
        headers={"x-forwarded-for": clean_ip, "x-forwarded-proto": "https"}
    )
    assert res_auth.status == 200, f"Expected 200, got {res_auth.status}"
    auth_data = res_auth.json()
    auth_token = auth_data.get("token")
    assert auth_token and len(auth_token) == 64, "Missing valid 32-byte hex clearance token"

    # Verify cookie attributes
    set_cookie = res_auth.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower(), f"Cookie must have HttpOnly: {set_cookie}"
    assert "secure" in set_cookie.lower(), f"Cookie must have Secure flag under HTTPS: {set_cookie}"
    assert "samesite=lax" in set_cookie.lower(), f"Cookie must have SameSite=lax: {set_cookie}"
    print("  ✅ PASS: Clearance token issued; Cookie hardened with HttpOnly, Secure, and SameSite=lax.")

    # ---------------------------------------------------------
    # TEST 7: Authorized WebSocket Connection with Clearance Token
    # ---------------------------------------------------------
    print("\n[TEST 7] Authorized WebSocket Handshake...")
    ws_events = await client.websocket_connect("/ws", query_string=f"token={auth_token}")
    accepted = any(e.get("type") == "websocket.accept" for e in ws_events)
    assert accepted, f"Authorized WebSocket should be accepted. Events: {ws_events}"
    # Verify telemetry broadcast event was sent
    data_events = [e for e in ws_events if e.get("type") == "websocket.send"]
    assert len(data_events) > 0, "Expected initial telemetry payload on WebSocket connection"
    init_payload = json.loads(data_events[0].get("text", "{}"))
    assert init_payload.get("type") == "initial"
    assert init_payload.get("telemetry", {}).get("processed") == 42
    print("  ✅ PASS: Authorized WebSocket successfully accepted; telemetry received.")

    # ---------------------------------------------------------
    # TEST 8: SSRF & Path Traversal Sanitization
    # ---------------------------------------------------------
    print("\n[TEST 8] Input Sanitization & SSRF Defense...")
    res_dex = await client.request(
        "GET",
        "/api/crypto/dexscreener?address=../../internal%00bypass",
        headers={"x-owner-token": auth_token}
    )
    assert res_dex.status == 200
    dex_data = res_dex.json()
    # Confirm invalid path characters are stripped
    assert ".." not in dex_data.get("address", "")
    assert "/" not in dex_data.get("address", "")
    print("  ✅ PASS: DexScreener URL input sanitized; path traversal and null bytes eliminated.")

    # ---------------------------------------------------------
    # TEST 9: Payload Size Limiting (>10MB Protection)
    # ---------------------------------------------------------
    print("\n[TEST 9] DoS Payload Size Limiting (>10MB Protection)...")
    res_large = await client.request(
        "POST",
        "/api/owner/verify",
        body=b"x",
        headers={"content-length": str(15 * 1024 * 1024)}
    )
    assert res_large.status == 413, f"Expected 413 Payload Too Large, got {res_large.status}"
    print("  ✅ PASS: Request exceeding 10MB immediately rejected with HTTP 413.")

    # ---------------------------------------------------------
    # TEST 10: Auto-Upgrade of Legacy Plaintext PINs
    # ---------------------------------------------------------
    print("\n[TEST 10] Automatic Migration of Legacy Plaintext PINs...")
    # Simulate a legacy plaintext PIN in .env and environment
    os.environ["OWNER_PIN"] = "5555"
    with open(ROOT_DIR / ".env", "w") as f:
        f.write("OWNER_PIN=5555\n")

    # Reload and verify Config sees legacy PIN
    current = Config.get_owner_pin()
    assert current == "5555"

    # Authenticate via verify_owner_pin
    fresh_ip = "192.0.2.12"
    res_migrate = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"pin": "5555"},
        headers={"x-forwarded-for": fresh_ip}
    )
    assert res_migrate.status == 200

    # Verify that the stored PIN has been automatically migrated to PBKDF2 hash on disk!
    migrated_pin = Config.get_owner_pin()
    assert migrated_pin.startswith("pbkdf2_sha256$600000$"), f"Expected auto-migrated PBKDF2 hash, got {migrated_pin}"
    assert Config.verify_pin("5555", migrated_pin) is True
    print("  ✅ PASS: Legacy plaintext PIN automatically upgraded to PBKDF2-HMAC-SHA256 hash on disk.")

    print("\n" + "=" * 70)
    print("🏆 ALL 10 COMPREHENSIVE SECURITY TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_security_tests())
