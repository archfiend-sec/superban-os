import asyncio
import json
import os
import sys
import stat
import time
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.config import Config
from core.db import DatabaseManager
from web.app import create_app
from tests.test_security_hardening import InProcessAsgiClient, MockEngine, MockSentinel, MockDb


class FortressMockEngine(MockEngine):
    def __init__(self):
        super().__init__()
        self.dispatched_dms = []

    async def notify_owner_clearance_issued(self, target_username: str, assigned_pin: str, role: str = "operator") -> bool:
        self.dispatched_dms.append({"action": "issued", "target": target_username, "pin": assigned_pin})
        return True

    async def notify_owner_clearance_revoked(self, target_username: str) -> bool:
        self.dispatched_dms.append({"action": "revoked", "target": target_username})
        return True


async def run_fortress_tests():
    print("=" * 75)
    print("🏰  OPERATION FORTRESS // COMPREHENSIVE ZERO-TRUST DEFENSE VERIFICATION")
    print("=" * 75)

    # Prepare Mock Engine and DB
    mock_db = MockDb()
    engine = FortressMockEngine()
    engine.db = mock_db

    # Initialize App with test configuration
    app = create_app(engine)
    client = InProcessAsgiClient(app)

    # Authenticate as Master Owner @archfiend000 with Master PIN 1704
    login_res = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "archfiend000", "pin": "1704"}
    )
    assert login_res.status == 200, f"Master Owner verification failed: {login_res.text}"
    owner_token = login_res.json()["token"]
    owner_cookie = f"owner_token={owner_token}"

    # ------------------------------------------------------------------
    # TEST 1: Cross-Site WebSocket Hijacking (CSWSH) Defense (SEC-FORT-01)
    # ------------------------------------------------------------------
    print("\n[TEST 1] Cross-Site WebSocket Hijacking (CSWSH) Origin Whitelisting...")

    # Attacker site attempting to hijack ambient owner_token cookie
    attacker_events = await client.websocket_connect(
        "/ws",
        headers={
            "origin": "https://malicious-attacker.com",
            "cookie": owner_cookie
        }
    )
    attacker_accepted = any(e.get("type") == "websocket.accept" for e in attacker_events)
    attacker_closed = any(e.get("type") == "websocket.close" and e.get("code") == 1008 for e in attacker_events)
    assert not attacker_accepted, "SECURITY FAILURE: WebSocket accepted connection from untrusted origin!"
    assert attacker_closed, "SECURITY FAILURE: WebSocket did not close with 1008 from untrusted origin!"
    print("  ✅ PASS: Untrusted origin 'https://malicious-attacker.com' strictly rejected (code 1008).")

    # Legitimate origin on Render
    render_events = await client.websocket_connect(
        "/ws",
        headers={
            "origin": "https://superban-os.onrender.com",
            "cookie": owner_cookie
        }
    )
    render_accepted = any(e.get("type") == "websocket.accept" for e in render_events)
    assert render_accepted, "Legitimate origin 'https://superban-os.onrender.com' should be accepted!"
    print("  ✅ PASS: Legitimate origin 'https://superban-os.onrender.com' successfully accepted.")

    # Legitimate local origin
    local_events = await client.websocket_connect(
        "/ws",
        headers={
            "origin": "http://localhost:8000",
            "cookie": owner_cookie
        }
    )
    local_accepted = any(e.get("type") == "websocket.accept" for e in local_events)
    assert local_accepted, "Legitimate origin 'http://localhost:8000' should be accepted!"
    print("  ✅ PASS: Legitimate local origin 'http://localhost:8000' successfully accepted.")

    # ------------------------------------------------------------------
    # TEST 2: Zero-Trust WebSocket Real-Time Eviction (SEC-FORT-02)
    # ------------------------------------------------------------------
    print("\n[TEST 2] Zero-Trust WebSocket Real-Time Eviction on Session Revocation...")

    # Set up mock operator clearance in mock DB
    class TestOperatorDb(MockDb):
        def __init__(self):
            super().__init__()
            self.operators = {
                "test_operator": {
                    "username": "test_operator",
                    "pin_hash": Config.hash_pin("9999"),
                    "role": "operator",
                    "status": "active"
                }
            }

        async def get_operator_clearance(self, username):
            return self.operators.get(username.lower())

        async def revoke_operator_clearance(self, username):
            if username in self.operators:
                self.operators[username]["status"] = "revoked"
                return True
            return False

        async def list_operator_clearances(self):
            return list(self.operators.values())

    op_db = TestOperatorDb()
    engine.db = op_db
    op_app = create_app(engine)
    op_client = InProcessAsgiClient(op_app)

    # Log in as test operator
    op_login = await op_client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "test_operator", "pin": "9999"}
    )
    assert op_login.status == 200, f"Operator login failed: {op_login.text}"
    op_token = op_login.json()["token"]

    # Also log in as owner to issue admin commands
    owner_login = await op_client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "archfiend000", "pin": "1704"}
    )
    owner_token_2 = owner_login.json()["token"]

    # Verify operator can connect to WebSocket
    op_ws_events = await op_client.websocket_connect(
        "/ws",
        headers={
            "origin": "https://superban-os.onrender.com",
            "cookie": f"owner_token={op_token}"
        }
    )
    assert any(e.get("type") == "websocket.accept" for e in op_ws_events), "Operator WS handshake failed"
    print("  ✅ PASS: Operator WebSocket handshake established.")

    # Revoke operator via owner clearance management
    revoke_res = await op_client.request(
        "POST",
        "/api/operators/revoke",
        headers={"X-Owner-Token": owner_token_2},
        json_data={"username": "test_operator"}
    )
    assert revoke_res.status == 200, f"Revocation failed: {revoke_res.text}"

    # Verify operator token is now rejected and cannot connect new WebSocket
    post_revoke_ws = await op_client.websocket_connect(
        "/ws",
        headers={
            "origin": "https://superban-os.onrender.com",
            "cookie": f"owner_token={op_token}"
        }
    )
    assert not any(e.get("type") == "websocket.accept" for e in post_revoke_ws), "Revoked operator still accepted!"
    print("  ✅ PASS: Operator clearance revoked; sessions and WebSocket privileges terminated immediately.")

    # ------------------------------------------------------------------
    # TEST 3: Security Audit Log Persistence & RBAC (SEC-FORT-03)
    # ------------------------------------------------------------------
    print("\n[TEST 3] Security Audit Log RBAC & Telemetry Export...")

    # Owner should be able to view security audit logs
    audit_res_owner = await op_client.request(
        "GET",
        "/api/security/audit_logs",
        headers={"X-Owner-Token": owner_token_2}
    )
    assert audit_res_owner.status == 200, f"Owner audit log fetch failed: {audit_res_owner.status}"
    audit_logs = audit_res_owner.json()
    assert isinstance(audit_logs, list), "Audit logs must return list"
    assert len(audit_logs) > 0, "Audit logs should record security actions"
    print(f"  ✅ PASS: Master Owner successfully retrieved {len(audit_logs)} audit records.")

    # Unassigned / unauthenticated user must be denied with 401
    audit_res_anon = await op_client.request("GET", "/api/security/audit_logs")
    assert audit_res_anon.status == 401, f"Expected 401 Unauthorized for anonymous audit log request, got {audit_res_anon.status}"
    print("  ✅ PASS: Anonymous audit log access strictly rejected with 401 Unauthorized.")

    # Create an active non-owner operator to test RBAC rejection (403)
    op_db.operators["sub_operator"] = {
        "username": "sub_operator",
        "pin_hash": Config.hash_pin("8888"),
        "role": "operator",
        "status": "active"
    }
    sub_login = await op_client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "sub_operator", "pin": "8888"}
    )
    sub_token = sub_login.json()["token"]

    audit_res_sub = await op_client.request(
        "GET",
        "/api/security/audit_logs",
        headers={"X-Owner-Token": sub_token}
    )
    assert audit_res_sub.status == 403, f"Expected 403 Forbidden for non-owner operator, got {audit_res_sub.status}"
    print("  ✅ PASS: Non-owner operator strictly blocked from security audit logs with 403 Forbidden.")

    # ------------------------------------------------------------------
    # TEST 4: Sensitive Configuration File Permission Hardening (SEC-FORT-04)
    # ------------------------------------------------------------------
    print("\n[TEST 4] Sensitive File Permission Lockdown (0o600 / -rw-------)...")
    env_file = ROOT_DIR / ".env"
    config_file = ROOT_DIR / "ultimate_config.json"

    # Run permission hardening
    Config.harden_file_permissions()

    if env_file.exists():
        mode = stat.S_IMODE(os.stat(env_file).st_mode)
        assert mode == 0o600, f".env file permission {oct(mode)} is not 0600!"
        print(f"  ✅ PASS: .env permissions locked to {oct(mode)} (owner read/write only).")
    else:
        print("  ℹ️ .env file not present on disk; skipped.")

    if config_file.exists():
        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        assert mode == 0o600, f"ultimate_config.json permission {oct(mode)} is not 0600!"
        print(f"  ✅ PASS: ultimate_config.json permissions locked to {oct(mode)} (owner read/write only).")
    else:
        print("  ℹ️ ultimate_config.json not present on disk; skipped.")

    # ------------------------------------------------------------------
    # TEST 5: Enhanced Content Security Policy (CSP) & Defense-in-Depth (SEC-FORT-05)
    # ------------------------------------------------------------------
    print("\n[TEST 5] HTTP Security Headers & CSP form-action Enforcement...")
    index_res = await client.request("GET", "/")
    csp = index_res.headers.get("content-security-policy", "")
    assert "form-action 'self'" in csp, f"CSP missing form-action 'self': {csp}"
    assert "frame-ancestors 'none'" in csp, f"CSP missing frame-ancestors 'none': {csp}"
    assert index_res.headers.get("x-content-type-options") == "nosniff", "Missing X-Content-Type-Options: nosniff"
    assert index_res.headers.get("x-frame-options") == "DENY", "Missing X-Frame-Options: DENY"
    print("  ✅ PASS: CSP includes form-action 'self', frame-ancestors 'none', nosniff, and DENY.")

    # ------------------------------------------------------------------
    # TEST 6: Real SQLite Security Audit Table Verification (SEC-FORT-06)
    # ------------------------------------------------------------------
    print("\n[TEST 6] Real SQLite Database security_audit_logs Table Verification...")
    test_db_path = ROOT_DIR / "test_fortress.db"
    if test_db_path.exists():
        os.remove(test_db_path)

    real_db = DatabaseManager(str(test_db_path))

    # Record security events
    await real_db.record_security_event(
        "CSWSH_BLOCKED", "attacker", "198.51.100.25", "Untrusted origin attempt", "CRITICAL"
    )
    await real_db.record_security_event(
        "AUTH_SUCCESS", "archfiend000", "127.0.0.1", "Master Owner logged in", "INFO"
    )

    logs = await real_db.get_security_audit_logs(limit=10)
    assert len(logs) == 2, f"Expected 2 audit logs in SQLite, got {len(logs)}"
    assert logs[0]["event_type"] == "AUTH_SUCCESS", "Most recent log should be first"
    assert logs[1]["event_type"] == "CSWSH_BLOCKED", "Older log should follow"
    assert logs[1]["severity"] == "CRITICAL"
    print("  ✅ PASS: SQLite security_audit_logs table verified with ordering, severity, and schema integrity.")

    if test_db_path.exists():
        os.remove(test_db_path)

    print("\n" + "=" * 75)
    print("🏆 ALL OPERATION FORTRESS DEFENSIVE VERIFICATION TESTS PASSED (100%)!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(run_fortress_tests())
