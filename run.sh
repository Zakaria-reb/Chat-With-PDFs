#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m uvicorn app.main:app --port 8000
