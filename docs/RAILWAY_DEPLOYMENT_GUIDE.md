# 🚀 Railway Deployment Guide (Docker)

This guide walks you through deploying the **GupShup Telegram Matchmaker** bot on [Railway](https://railway.com) using the production Dockerfile.

---

## 1. Architectural Overview on Railway

A complete production deployment on Railway consists of **3 services** within a single Railway project:

```
┌────────────────────────────────────────────────────────┐
│                    Railway Project                     │
│                                                        │
│  ┌───────────────────┐        ┌─────────────────────┐  │
│  │    PostgreSQL     │        │        Redis        │  │
│  │ (Managed Service) │        │  (Managed Service)  │  │
│  └─────────▲─────────┘        └──────────▲──────────┘  │
│            │                             │             │
│            │  DATABASE_URL               │ REDIS_URL   │
│            └──────────────┬──────────────┘             │
│                           │                            │
│                 ┌─────────┴─────────┐                  │
│                 │   GupShup Bot     │                  │
│                 │  (via Dockerfile) │                  │
│                 └─────────▲─────────┘                  │
│                           │ Telegram Long-Polling      │
└───────────────────────────┼────────────────────────────┘
                            │
                    Telegram Servers
```

---

## 2. Prerequisites

1. A **GitHub** account with your `telegram-matchmaker` code pushed to a repository.
2. A **Railway** account at [railway.com](https://railway.com).
3. Your Telegram Bot Token from [@BotFather](https://t.me/BotFather).

---

## 3. Step-by-Step Deployment via Web Dashboard

### Step 3.1: Create a New Project on Railway
1. Log in to [Railway](https://railway.com).
2. Click **"+ New Project"** in the top right.
3. Select **"Deploy from GitHub repo"**.
4. Choose your `telegram-matchmaker` repository.
5. Railway will automatically detect the [Dockerfile](file:///a:/telegram-matchmaker/Dockerfile) and [railway.json](file:///a:/telegram-matchmaker/railway.json).

---

### Step 3.2: Add Managed PostgreSQL
1. Inside your Railway project canvas, click **"+ New"** (or press `Ctrl + K` / `Cmd + K`).
2. Select **"Database"** → **"Add PostgreSQL"**.
3. Railway will provision a managed PostgreSQL database in ~10 seconds.

---

### Step 3.3: Add Managed Redis
1. In the same project canvas, click **"+ New"**.
2. Select **"Database"** → **"Add Redis"**.
3. Railway will provision a high-performance Redis instance in ~10 seconds.

---

### Step 3.4: Configure Environment Variables
Click on your **GupShup Bot service** card in the Railway dashboard, navigate to the **"Variables"** tab, and configure the following:

| Variable | Recommended Value on Railway | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `8627424809:AAEc...` *(your real bot token)* | Token from @BotFather |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Railway dynamic reference to your Postgres service |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Railway dynamic reference to your Redis service |
| `ENVIRONMENT` | `production` | Switches bot to production mode |

> [!TIP]
> **Automatic `asyncpg` Conversion**:  
> Railway's Postgres plugin usually provides `DATABASE_URL` in `postgresql://` format. Our `app/config/settings.py` includes an automatic validator that converts any `postgres://` or `postgresql://` into `postgresql+asyncpg://` automatically!

---

### Step 3.5: Deployment & Database Migrations
Once environment variables are saved, Railway triggers a build automatically:
1. Docker builds using [Dockerfile](file:///a:/telegram-matchmaker/Dockerfile).
2. [entrypoint.sh](file:///a:/telegram-matchmaker/entrypoint.sh) runs:
   ```bash
   alembic upgrade head   # Applies all database tables and indexes
   exec python app/main.py  # Starts bot, matching worker, and janitor
   ```
3. Your bot will come online and begin receiving messages!

---

## 4. Alternative: Deploy Using the Railway CLI

If you prefer deploying directly from your terminal:

```powershell
# 1. Install Railway CLI (via npm or scoop/brew)
npm i -g @railway/cli

# 2. Login to your Railway account
railway login

# 3. Link or create project
railway init

# 4. Add PostgreSQL and Redis
railway add --plugin postgresql
railway add --plugin redis

# 5. Set your Telegram Bot Token
railway variables set TELEGRAM_BOT_TOKEN="8627424809:AAEcR0SEOBjgWOOvd1DIF5zQgKU7Mdr6hUw"
railway variables set ENVIRONMENT="production"

# 6. Deploy Docker container
railway up
```

---

## 5. Verifying Deployment Logs

Click the **"View Logs"** / **"Deployments"** tab on your Bot service in Railway. You should see:

```text
====================================================
🚀 Running database migrations (alembic upgrade head)...
====================================================
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 001_initial_schema
INFO  [alembic.runtime.migration] Running upgrade 001_initial_schema -> 002_add_user_profiles
INFO  [alembic.runtime.migration] Running upgrade 002_add_user_profiles -> 003_add_user_preferences
====================================================
🤖 Starting GupShup Telegram Matchmaker Bot...
====================================================
INFO  | __main__ | Starting GupShup matchmaker in production mode...
INFO  | app.infrastructure.redis | Redis connection verified: ping response=True
INFO  | app.infrastructure.telegram | Telegram UI: 9 bot commands registered, menu button enabled.
INFO  | app.core.matching.worker | MatchmakingWorker started with tick interval 1.00s
INFO  | app.core.matching.worker | ReconciliationJanitor started with interval 60.0s
INFO  | aiogram.dispatcher | Run polling for bot @GupShupNowBot id=8627424809 - 'Gupshup'
```

---

## 6. Key Configuration Files Reference

- [Dockerfile](file:///a:/telegram-matchmaker/Dockerfile): Slim Debian container with non-root security.
- [entrypoint.sh](file:///a:/telegram-matchmaker/entrypoint.sh): Automated migration runner and bot executor.
- [railway.json](file:///a:/telegram-matchmaker/railway.json): Explicit Dockerfile build config for Railway.
- [.dockerignore](file:///a:/telegram-matchmaker/.dockerignore): Excludes virtual environment and temporary files.
- [app/config/settings.py](file:///a:/telegram-matchmaker/app/config/settings.py): Auto-normalizes Railway database URLs.
