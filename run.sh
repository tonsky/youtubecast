#!/bin/sh
# Upgrades yt-dlp (YouTube breaks old versions often), then checks sources.
# Expects venv/ from: python3 -m venv venv && venv/bin/pip install -r requirements.txt
set -e
cd "$(dirname "$0")"
venv/bin/pip install -q --disable-pip-version-check --upgrade -r requirements.txt
exec venv/bin/python youtubecast.py
