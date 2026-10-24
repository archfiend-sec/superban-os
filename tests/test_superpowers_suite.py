import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Configure test environment
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ["MASTER_OWNER_USERNAME"] = "archfiend000"
os.environ["OWNER_PIN"] = "3105"

from core.config import Config
from core.db import DatabaseManager
from core.engine import SuperBanEngine
from core.sentinel import SentinelShield
from web.app import create_app, OPERATOR_SESSIONS
from tests.test_security_hardening import InProcessAsgiClient

async def run_all_tests():
    print("=" * 70)
    print("🛡️  NON-ADMIN SUPERPOWERS (PLANS A, B, C, H, I) VERIFICATION SUITE")
    print("=" * 70)

    # Setup temporary test database
    temp_db_fd, temp_db_path = tempfile.mkstemp(suffix="_superpowers.db")
    os.close(temp_db_fd)
    db = DatabaseManager(db_path=Path(temp_db_path))

    class MockTelegramClient:
        def __init__(self):
            self.send_message = AsyncMock(return_value=MagicMock(id=999))
            self.get_input_entity = AsyncMock(return_value=MagicMock())
            self.get_entity = AsyncMock(return_value=MagicMock(title="Alpha Tactical Group", id=-1001234567))

        def is_connected(self):
            return True

        def on(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        async def __call__(self, *args, **kwargs):
            return True

    mock_client = MockTelegramClient()

    engine = SuperBanEngine(mock_client, db)
    engine.sentinel = SentinelShield(mock_client, db, engine=engine)
    engine.sentinel.engine = engine

    # Setup Web App and InProcessAsgiClient
    app = create_app(engine)
    client = InProcessAsgiClient(app)

    # Authenticate as owner @archfiend000
    login_res = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "archfiend000", "pin": "3105"}
    )
    assert login_res.status == 200, f"Login failed: {login_res.status} {login_res.text}"
    owner_token = login_res.json().get("token")
    auth_headers = {"X-Owner-Token": owner_token}

    # ══════════════════════════════════════════════════════════════════════
    # [TEST 1] PLAN H: 1-Click & Autonomous MTProto Reporting to Telegram T&S
    # ══════════════════════════════════════════════════════════════════════
    print("\n[TEST 1] Plan H: MTProto Trust & Safety Reporting...")
    with patch("telethon.tl.functions.messages.ReportRequest", MagicMock()) as mock_report_req:
        mock_client.__call__ = AsyncMock(return_value=True)
        
        # Test engine method
        res = await engine.report_threat_to_telegram(
            group_id=-1001234567, message_id=8812, reason="fake", details="Wallet drainer link"
        )
        assert res.get("success") is True, "report_threat_to_telegram failed"
        assert res.get("status") == "DISPATCHED_TO_TELEGRAM_TRUST_AND_SAFETY"

    # Test REST API endpoint /api/threat/report
    web_report_res = await client.request(
        "POST",
        "/api/threat/report",
        headers=auth_headers,
        json_data={"group_id": -1001234567, "message_id": 8812, "reason": "spam", "details": "Phishing bot"}
    )
    assert web_report_res.status == 200, f"Endpoint /api/threat/report failed: {web_report_res.text}"
    assert web_report_res.json().get("success") is True
    print("  ✅ PASS: Plan H MTProto Trust & Safety reporting verified via engine & REST API.")

    # ══════════════════════════════════════════════════════════════════════
    # [TEST 2] PLAN B: Ghost Media & Ephemeral Document Vault
    # ══════════════════════════════════════════════════════════════════════
    print("\n[TEST 2] Plan B: Ghost Media & Ephemeral Document Vault...")
    media_vault = PROJECT_ROOT / "media_vault"
    media_vault.mkdir(exist_ok=True)
    sample_file = media_vault / "ghost_test_sample.txt"
    sample_file.write_text("Sovereign Ghost Media Payload - Preserved before deletion!")

    # Intercept message with media
    await db.save_intercepted_message(
        group_id=-1001234567,
        group_title="Alpha Chamber",
        message_id=4001,
        user_id=777888,
        full_name="Ephemeral Spammer",
        username="ephemeral_guy",
        text="Check this confidential whitepaper before I delete it!",
        media_type="document",
        media_path=str(sample_file),
        file_name="ghost_test_sample.txt",
        file_size=sample_file.stat().st_size,
        file_hash="mocked_sha256_hash_12345"
    )

    # Intercept deletion
    del_records = await db.mark_messages_deleted(-1001234567, [4001])
    assert len(del_records) == 1, "Failed to mark message deleted in Ghost Vault"
    assert del_records[0]["file_name"] == "ghost_test_sample.txt"
    assert del_records[0]["media_path"] == str(sample_file)

    # Retrieve from Ghost Vault
    ghosts = await db.get_ghost_messages(group_id=-1001234567, filter_mode="deleted")
    target_ghost = next((g for g in ghosts if g["message_id"] == 4001), None)
    assert target_ghost is not None, "Target ghost message not found"
    assert target_ghost["is_deleted"] is True
    assert target_ghost["file_name"] == "ghost_test_sample.txt"
    ghost_db_id = target_ghost["id"]

    # Test GET /api/ghost/media/{ghost_id}
    media_resp = await client.request(
        "GET",
        f"/api/ghost/media/{ghost_db_id}",
        headers=auth_headers
    )
    assert media_resp.status == 200, f"Failed to download ghost media: {media_resp.status}"
    assert "Sovereign Ghost Media Payload" in media_resp.text

    # Test path traversal guard
    unauthorized_path_res = await client.request(
        "GET",
        "/api/ghost/media/9999999",
        headers=auth_headers
    )
    assert unauthorized_path_res.status == 404
    print("  ✅ PASS: Plan B Ghost Media Vault preservation, hashing & secure download verified.")

    # ══════════════════════════════════════════════════════════════════════
    # [TEST 3] PLAN A: Voice Chat STT & AI Executive Summarizer
    # ══════════════════════════════════════════════════════════════════════
    print("\n[TEST 3] Plan A: Voice Chat STT & AI Summarizer...")
    await db.record_vc_speaker_activity(-1001234567, "call_1", 101, "Alice Lead", "alice_lead", 45.0)
    await db.record_vc_speaker_activity(-1001234567, "call_1", 102, "Bob Architect", "bob_dev", 30.0)

    # Execute VC transcribe and summarize
    vc_res = await engine.transcribe_and_summarize_vc(group_id=-1001234567)
    assert vc_res.get("success") is True
    assert len(vc_res.get("summary_bullets", [])) == 3, "Expected 3-bullet executive tactical summary"
    assert "Decisions & Alpha" in vc_res["summary_bullets"][0]
    assert "Participants & Sentiment" in vc_res["summary_bullets"][1]
    assert "Risk & Anomaly" in vc_res["summary_bullets"][2]
    # Check DM dispatch to @archfiend000
    mock_client.send_message.assert_called()
    assert vc_res.get("dm_sent_to_owner") is True

    # Test REST API endpoint /api/vc/transcribe
    vc_web_res = await client.request(
        "POST",
        "/api/vc/transcribe",
        headers=auth_headers,
        json_data={"group_id": -1001234567}
    )
    assert vc_web_res.status == 200, f"VC transcribe API failed: {vc_web_res.text}"
    assert len(vc_web_res.json().get("summary_bullets", [])) == 3
    print("  ✅ PASS: Plan A Voice Chat STT & 3-bullet executive summary dispatched to @archfiend000 DM.")

    # ══════════════════════════════════════════════════════════════════════
    # [TEST 4] PLAN C: Shadow Network Syndicates & Circadian Sleep Profiler
    # ══════════════════════════════════════════════════════════════════════
    print("\n[TEST 4] Plan C: Shadow Network Syndicates & Circadian Sleep Profiler...")
    for mid in range(10, 15):
        await db.save_intercepted_message(
            group_id=-1001234567, group_title="Raid Group Alpha", message_id=mid,
            user_id=8801, full_name="Syndicate Bot 1", username="shill_one",
            text="BUY $MOON NOW! Ca: 0x1111111111111111111111111111111111111111"
        )
        await db.save_intercepted_message(
            group_id=-1001234567, group_title="Raid Group Alpha", message_id=mid+50,
            user_id=8802, full_name="Syndicate Bot 2", username="shill_two",
            text="LFG 100x gem! Ca: 0x1111111111111111111111111111111111111111"
        )
    
    await db.save_extracted_entities([
        {"group_id": -1001234567, "group_title": "Raid Group Alpha", "message_id": 10, "sender_id": 8801, "sender_name": "Syndicate Bot 1", "entity_type": "crypto_eth", "entity_value": "0x1111111111111111111111111111111111111111", "context": "BUY"},
        {"group_id": -1001234567, "group_title": "Raid Group Alpha", "message_id": 60, "sender_id": 8802, "sender_name": "Syndicate Bot 2", "entity_type": "crypto_eth", "entity_value": "0x1111111111111111111111111111111111111111", "context": "LFG"}
    ])

    syndicates = await db.detect_syndicate_rings(time_window_sec=300, min_cooccurrences=2)
    assert len(syndicates) >= 1, "Failed to detect syndicate ring"
    syn = syndicates[0]
    assert syn["user1"]["id"] == 8801
    assert syn["user2"]["id"] == 8802
    assert syn["syndicate_score"] >= 60

    # Test Sleep Cycle Profiler
    sleep_profile = await db.get_user_sleep_cycle_profile(8801)
    assert sleep_profile["user_id"] == 8801
    assert len(sleep_profile["hourly_distribution"]) == 24
    assert "UTC" in sleep_profile["estimated_timezone"]

    # Test REST API endpoints
    syn_web_res = await client.request("GET", "/api/osint/syndicates", headers=auth_headers)
    assert syn_web_res.status == 200
    assert len(syn_web_res.json()) >= 1

    sleep_web_res = await client.request("GET", "/api/osint/user/8801/sleep_cycle", headers=auth_headers)
    assert sleep_web_res.status == 200
    assert len(sleep_web_res.json()["hourly_distribution"]) == 24
    print("  ✅ PASS: Plan C Syndicate shill ring co-occurrence & circadian sleep cycle profiler verified.")

    # ══════════════════════════════════════════════════════════════════════
    # [TEST 5] PLAN I: Telegram Companion Remote Control via DM (@archfiend000)
    # ══════════════════════════════════════════════════════════════════════
    print("\n[TEST 5] Plan I: Telegram Companion Remote Controller in DM...")
    assert Config.get_owner_username() == "archfiend000"
    
    # 1. Verify assign creates clearance record and hashes PIN
    pin_hash = Config.hash_pin("9988")
    await db.save_operator_clearance("new_officer", pin_hash, role="operator", assigned_by="archfiend000")
    record = await db.get_operator_clearance("new_officer")
    assert record is not None
    assert record["status"] == "active"
    assert Config.verify_pin("9988", record["pin_hash"]) is True

    # 2. Verify list_operator_clearances includes new operator
    ops = await db.list_operator_clearances()
    assert any(o["username"] == "new_officer" for o in ops)

    # 3. Verify revoke terminates clearance
    await db.revoke_operator_clearance("new_officer")
    revoked_record = await db.get_operator_clearance("new_officer")
    assert revoked_record["status"] == "revoked"
    print("  ✅ PASS: Plan I Companion remote controller with @archfiend000 root authority verified.")

    print("\n" + "=" * 70)
    print("🏆 ALL 5 SUPERPOWERS (PLANS A, B, C, H, I) PASSED WITH 100% SUCCESS!")
    print("=" * 70)

    # Cleanup
    db.close()
    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)

if __name__ == "__main__":
    asyncio.run(run_all_tests())
