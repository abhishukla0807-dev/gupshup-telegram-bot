#!/usr/bin/env bash
set -e

echo "===================================================="
echo "🚀 Running database migrations (alembic upgrade head)..."
echo "===================================================="
alembic upgrade head

echo "===================================================="
echo "🤖 Starting GupShup Telegram Matchmaker Bot..."
echo "===================================================="
exec python app/main.py
