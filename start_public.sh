#!/usr/bin/env bash
# ==============================================================================
#  SUPERBAN OS // SOVEREIGN APEX — ONE-COMMAND PRODUCTION BOOTSTRAPPER
#  Starts Web Mission Control & Creates a Public Cloudflare HTTPS Tunnel
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "=================================================================="
echo "  ⚡ SUPERBAN OS // SOVEREIGN APEX LAUNCHER"
echo "=================================================================="

# Kill any stale port 8000 processes
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
sleep 1

# Start the Web Mission Control Server
echo "🌐 Starting Mission Control on port 8000..."
./venv/bin/python3 web_app.py > /tmp/superban_web.log 2>&1 &
WEB_PID=$!
echo "   PID: $WEB_PID (logs: /tmp/superban_web.log)"

# Wait for local server to be ready
echo "⏳ Waiting for local server to initialize..."
for i in {1..15}; do
    if curl -s http://127.0.0.1:8000/api/status >/dev/null 2>&1; then
        echo "✅ Mission Control is running locally on http://127.0.0.1:8000"
        break
    fi
    sleep 1
done

# Check if standalone cloudflared exists
if [ -x "$DIR/bin/cloudflared" ]; then
    echo "🔒 Initializing secure Cloudflare edge tunnel..."
    "$DIR/bin/cloudflared" tunnel --url http://127.0.0.1:8000 > /tmp/tunnel.log 2>&1 &
    TUNNEL_PID=$!
    
    echo "⏳ Establishing edge route..."
    TUNNEL_URL=""
    for i in {1..20}; do
        TUNNEL_URL=$(grep -o 'https://[-a-zA-Z0-9\.]*\.trycloudflare\.com' /tmp/tunnel.log 2>/dev/null | tail -n 1 || true)
        if [ -n "$TUNNEL_URL" ]; then
            break
        fi
        sleep 1
    done

    if [ -n "$TUNNEL_URL" ]; then
        echo "$TUNNEL_URL" > "$DIR/PUBLIC_URL.txt"
        echo ""
        echo "=================================================================="
        echo "🚀 SYSTEM DEPLOYED ONLINE! ACCESSIBLE FROM ANY DEVICE:"
        echo "   $TUNNEL_URL"
        echo "=================================================================="
        echo "Saved to: $DIR/PUBLIC_URL.txt"
    else
        echo "⚠️ Cloudflare tunnel initializing in background (check /tmp/tunnel.log)"
    fi
else
    echo "ℹ️  Standalone cloudflared not found in bin/. Run with local port 8000."
fi

echo ""
echo "Press Ctrl+C to terminate or leave running in background."
wait
