#!/usr/bin/env bash
# ==============================================================================
#  SUPERBAN OS v5.0 APEX // WEB MISSION CONTROL ONE-CLICK RUNNER
# ==============================================================================
cd "/Users/archfiend/.gemini/antigravity/scratch/telegram_group_manager" || exit 1
lsof -ti:8000 | xargs kill -9 2>/dev/null
exec ./venv/bin/python3 web_app.py
