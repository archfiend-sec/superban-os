import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.config import Config
from core.db import DatabaseManager
from web.app import create_app
from tests.test_security_hardening import InProcessAsgiClient

class MockSentinel:
    def register_callback(self, cb):
        pass

class MockEngine:
    def __init__(self, db):
        self.sentinel = MockSentinel()
        self.telemetry = {"state": "IDLE"}
        self.db = db
        self.client = None
        self.dispatched_dms = []

    def register_listener(self, cb):
        pass

    async def notify_owner_clearance_issued(self, target_username: str, assigned_pin: str, role: str = "operator") -> bool:
        self.dispatched_dms.append({"action": "issued", "target": target_username, "pin": assigned_pin})
        return True

    async def notify_owner_clearance_revoked(self, target_username: str) -> bool:
        self.dispatched_dms.append({"action": "revoked", "target": target_username})
        return True

async def run_tests():
    print("=" * 70)
    print("🛡️  MULTI-OPERATOR RBAC & OWNER CLEARANCE VERIFICATION SUITE")
    print("=" * 70)

    test_db_path = ROOT_DIR / "test_clearance.db"
    if test_db_path.exists():
        test_db_path.unlink()

    db = DatabaseManager(db_path=test_db_path)
    mock_engine = MockEngine(db)
    app = create_app(mock_engine)
    client = InProcessAsgiClient(app)

    # TEST 1: Owner authentication with @archfiend000 and code 1704
    print("\n[TEST 1] Owner @archfiend000 clearance verification...")
    # Verify old passcode 3105 is strictly rejected
    res_old = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "@archfiend000", "pin": "3105"}
    )
    assert res_old.status == 403, "Old passcode 3105 must be rejected with 403 Forbidden"

    # Verify new passcode 1704 is accepted
    res_owner = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "@archfiend000", "pin": "1704"}
    )
    assert res_owner.status == 200, f"Owner login failed: {res_owner.status} {res_owner.text}"
    owner_data = res_owner.json()
    assert owner_data.get("role") == "owner"
    assert owner_data.get("username") == "archfiend000"
    owner_token = owner_data.get("token")
    print("  ✅ PASS: Master Owner @archfiend000 authenticated with 1704; role='owner'.")

    # TEST 2: Unauthorized user rejection
    print("\n[TEST 2] Unauthorized operator denial...")
    res_unauth = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "random_hacker", "pin": "9999"}
    )
    assert res_unauth.status == 403, f"Expected 403, got {res_unauth.status}"
    print("  ✅ PASS: Unassigned user strictly denied access.")

    # TEST 3: Owner assigns new operator clearance
    print("\n[TEST 3] Owner granting operator clearance & DM dispatch...")
    res_assign = await client.request(
        "POST",
        "/api/operators/assign",
        headers={"X-Owner-Token": owner_token},
        json_data={"username": "@shadow_operative", "pin": "4321"}
    )
    assert res_assign.status == 200, f"Assign failed: {res_assign.status} {res_assign.text}"
    assert len(mock_engine.dispatched_dms) == 1
    assert mock_engine.dispatched_dms[0]["target"] == "shadow_operative"
    assert mock_engine.dispatched_dms[0]["pin"] == "4321"
    print("  ✅ PASS: Operator @shadow_operative granted code 4321; DM alert generated.")

    # TEST 4: Newly assigned operator login
    print("\n[TEST 4] Assigned operator authentication...")
    res_op_login = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "shadow_operative", "pin": "4321"}
    )
    assert res_op_login.status == 200, f"Operator login failed: {res_op_login.status}"
    op_data = res_op_login.json()
    assert op_data.get("role") == "operator"
    op_token = op_data.get("token")
    print("  ✅ PASS: Operator logged in successfully; role='operator'.")

    # TEST 5: Operator prevented from assigning other operators
    print("\n[TEST 5] Non-owner access control enforcement...")
    res_forbidden = await client.request(
        "POST",
        "/api/operators/assign",
        headers={"X-Owner-Token": op_token},
        json_data={"username": "malicious_sub_user", "pin": "1234"}
    )
    assert res_forbidden.status == 403, f"Expected 403 Forbidden, got {res_forbidden.status}"
    print("  ✅ PASS: Non-owner operator strictly blocked from managing clearances (403 Forbidden).")

    # TEST 6: Owner revokes operator clearance
    print("\n[TEST 6] Revocation and active session termination...")
    res_revoke = await client.request(
        "POST",
        "/api/operators/revoke",
        headers={"X-Owner-Token": owner_token},
        json_data={"username": "shadow_operative"}
    )
    assert res_revoke.status == 200

    # Old token must now be invalid
    res_probe = await client.request(
        "GET",
        "/api/groups",
        headers={"X-Owner-Token": op_token}
    )
    assert res_probe.status == 401, f"Expected 401 Unauthorized for revoked session, got {res_probe.status}"

    # New login attempt must also fail
    res_relogin = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "shadow_operative", "pin": "4321"}
    )
    assert res_relogin.status == 403
    print("  ✅ PASS: Operator revoked, active sessions invalidated, relogin blocked.")

    # Clean up test db
    db.close()
    if test_db_path.exists():
        test_db_path.unlink()

    print("\n" + "=" * 70)
    print("🏆 ALL MULTI-OPERATOR ACCESS TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_tests())
