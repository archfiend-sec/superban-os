#!/usr/bin/env bash
lsof -ti:8000 | xargs kill -9 2>/dev/null
cd "$(dirname "$0")" || exit 1
exec ./venv/bin/python3 web_app.py
