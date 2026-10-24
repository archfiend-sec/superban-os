import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.engine import SuperBanEngine
from web.app import create_app, find_mp3_sync_offset, get_mp3_silence_chunk
from tests.test_security_hardening import InProcessAsgiClient

async def run_vc_streaming_verification():
    print("=" * 70)
    print("🎙️  VOICE CHAT (VC) AUDIO STREAMING & JITTER BUFFER VERIFICATION")
    print("=" * 70)

    # 1. Test Silence Chunk Generation
    print("\n[TEST 1] Testing dynamic MPEG Layer III silence chunk generator...")
    silence = get_mp3_silence_chunk()
    assert len(silence) > 0, "Silence chunk must not be empty"
    assert silence[0] == 0xFF and (silence[1] & 0xE0) == 0xE0, "Silence must start with valid MP3 frame sync"
    
    # Verify silence decodes cleanly with ffmpeg
    candidates = [
        shutil.which("ffmpeg"),
        str(PROJECT_ROOT / "venv" / "bin" / "ffmpeg")
    ]
    ffmpeg_bin = next((c for c in candidates if c and os.path.exists(c)), "ffmpeg")
    proc = subprocess.run([ffmpeg_bin, "-v", "error", "-i", "pipe:0", "-f", "null", "-"], input=silence, stderr=subprocess.PIPE)
    assert proc.returncode == 0, f"Silence decoding failed: {proc.stderr.decode()}"
    print(f"  ✅ PASS: Generated {len(silence)} bytes clean MP3 silence frames (0 decode errors).")

    # 2. Test MP3 Frame Sync Boundary Detection
    print("\n[TEST 2] Testing MP3 frame sync offset finder...")
    test_mp3 = "/tmp/test_sync_probe.mp3"
    subprocess.run([
        ffmpeg_bin, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-codec:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0", "-id3v2_version", "0",
        test_mp3
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    sz = os.path.getsize(test_mp3)
    assert sz > 2000, "Test MP3 file should be larger than 2KB"
    
    # Seek to an intentionally unaligned byte (e.g., byte 513)
    unaligned_target = 513
    aligned_offset = find_mp3_sync_offset(test_mp3, unaligned_target)
    assert aligned_offset >= unaligned_target
    with open(test_mp3, "rb") as f:
        f.seek(aligned_offset)
        sync_bytes = f.read(2)
        assert sync_bytes[0] == 0xFF and (sync_bytes[1] & 0xE0) == 0xE0, f"Byte at offset {aligned_offset} is not MP3 sync: {sync_bytes.hex()}"
    print(f"  ✅ PASS: Aligned unaligned offset {unaligned_target} to valid MP3 frame boundary {aligned_offset}.")

    # 3. Test REST & Streaming Endpoints in web/app.py
    print("\n[TEST 3] Testing /api/vc/audio_stream HEAD and GET endpoints...")
    mock_engine = MagicMock(spec=SuperBanEngine)
    mock_engine.is_in_voice_call = MagicMock(return_value=True)
    mock_engine.active_vc_session = {"group_id": -1001234567, "ssrc": 99887766, "server_connection": {}}
    mock_engine.register_listener = MagicMock()
    mock_engine.sentinel = MagicMock()
    mock_engine.sentinel.register_callback = MagicMock()
    
    app = create_app(mock_engine)
    client = InProcessAsgiClient(app)

    # Authenticate as Master Owner @archfiend000 with passcode 1704
    login_res = await client.request(
        "POST",
        "/api/owner/verify",
        json_data={"username": "archfiend000", "pin": "1704"}
    )
    assert login_res.status == 200, f"Login failed: {login_res.text}"
    token = login_res.json()["token"]
    auth_headers = {"X-Owner-Token": token}

    # HEAD request
    head_res = await client.request("HEAD", f"/api/vc/audio_stream?group_id=-1001234567&token={token}")
    assert head_res.status == 200, f"HEAD request failed: {head_res.status}"
    assert head_res.headers.get("content-type") == "audio/mpeg"
    assert head_res.headers.get("x-content-type-options") == "nosniff"
    print("  ✅ PASS: HEAD /api/vc/audio_stream verified with 200 OK and security headers.")

    # GET request with streaming response using query parameter token
    test_stream_path = "/tmp/vc_stream_1234567.mp3"
    shutil.copyfile(test_mp3, test_stream_path)
    
    get_res = await client.request("GET", f"/api/vc/audio_stream?group_id=-1001234567&token={token}")
    assert get_res.status == 200, f"GET request failed: {get_res.status}"
    assert get_res.headers.get("content-type") == "audio/mpeg"
    assert get_res.headers.get("connection") == "keep-alive"
    assert len(get_res.body) > 0, "Streamed response body must not be empty"

    # Verify first bytes are valid MP3 sync
    assert get_res.body[0] == 0xFF and (get_res.body[1] & 0xE0) == 0xE0, f"Invalid sync on first chunk: {get_res.body[:4].hex()}"
    print(f"  ✅ PASS: Successfully received {len(get_res.body)} bytes frame-aligned audio stream.")

    # Cleanup temporary test files
    for p in [test_mp3, test_stream_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    print("\n" + "=" * 70)
    print("🏆 ALL VC AUDIO STREAMING TESTS PASSED WITH 100% SUCCESS!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_vc_streaming_verification())
