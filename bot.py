"""
Main root entrypoint for GupShup Telegram Matchmaker Bot.
Enables running directly via `python bot.py` or via Docker container.
Ensures database migrations are applied before launching polling.
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import main


def run_migrations() -> None:
    """Execute Alembic migrations to ensure PostgreSQL tables are up to date."""
    try:
        print("====================================================", flush=True)
        print("🚀 Applying database migrations (alembic upgrade head)...", flush=True)
        print("====================================================", flush=True)
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(result.stdout, flush=True)
            print("✅ Database schema is up to date.", flush=True)
        else:
            print(f"⚠️ Migration output: {result.stdout}\n{result.stderr}", flush=True)
    except Exception as exc:
        print(f"⚠️ Note during migration check: {exc}", flush=True)


if __name__ == "__main__":
    run_migrations()
    asyncio.run(main())
