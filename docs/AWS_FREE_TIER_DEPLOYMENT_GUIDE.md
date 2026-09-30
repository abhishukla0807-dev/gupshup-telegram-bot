# ☁️ AWS Free Tier Deployment Guide

This guide walks you through deploying the **GupShup Telegram Matchmaker** bot on an **AWS EC2 Free Tier instance** (100% free for 12 months, zero unexpected charges).

---

## 1. AWS Free Tier Limits & Safety Guarantees

AWS provides a generous Free Tier for 12 months for new accounts. Here is how our architecture stays strictly within the free limits:

| Resource | AWS Free Tier Limit | Bot Consumption | Free Tier Status |
|---|---|---|---|
| **EC2 Compute** | **750 hours / month** of `t2.micro` or `t3.micro` | 1 instance running 24/7 = **744 hours max** | ✅ **100% FREE** |
| **EBS Storage** | **30 GB / month** (gp3/gp2 SSD) | ~8–10 GB (OS + Docker + DB data) | ✅ **100% FREE** |
| **Data Transfer** | **100 GB / month** outbound to Internet | < 500 MB / month (Telegram long-polling) | ✅ **100% FREE** |
| **PostgreSQL & Redis** | *Managed services (RDS/ElastiCache) have hidden traps* | Containerized on EC2 via Docker Compose | ✅ **100% FREE & ZERO Surprise Bills** |

> [!IMPORTANT]
> **Why we run PostgreSQL and Redis inside Docker Compose on EC2 instead of AWS RDS / ElastiCache:**
> - AWS **ElastiCache (Redis) has NO permanent free tier** and charges automatically if misconfigured.
> - Running `postgres:15-alpine` and `redis:7-alpine` inside Docker on the single EC2 instance consumes only ~150MB of RAM total, runs at near-zero latency over localhost, and saves persistent data to your free 30 GB EBS volume.
> - This guarantees **0 risk of surprise bills**.

---

## 2. Architecture Overview

```
                                      AWS CLOUD (Free Tier)
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  EC2 Instance (t2.micro or t3.micro - Ubuntu 24.04 LTS)                                │
│                                                                                        │
│   Security Group: Only Port 22 (SSH) Inbound                                           │
│                                                                                        │
│  ┌───────────────────────── Docker Network (Isolated) ───────────────────────────────┐  │
│  │                                                                                  │  │
│  │    ┌───────────────────┐               ┌─────────────────────┐                   │  │
│  │    │ matchmaker_postgres│              │   matchmaker_redis  │                   │  │
│  │    │  (Postgres 15)    │               │     (Redis 7)       │                   │  │
│  │    └─────────▲─────────┘               └──────────▲──────────┘                   │  │
│  │              │                                    │                              │  │
│  │              └─────────────────┬──────────────────┘                              │  │
│  │                                │ (internal bridge)                               │  │
│  │                      ┌─────────┴─────────┐                                       │  │
│  │                      │  matchmaker_bot   │                                       │  │
│  │                      │ (Python 3.11 Bot) │                                       │  │
│  │                      └─────────▲─────────┘                                       │  │
│  │                                │                                                 │  │
│  └────────────────────────────────┼─────────────────────────────────────────────────┘  │
│                                   │ Outbound HTTPS (Port 443)                           │
└───────────────────────────────────┼────────────────────────────────────────────────────┘
                                    ▼
                        Telegram Bot API (api.telegram.org)
```

---

## 3. Step-by-Step EC2 Setup

### Step 3.1: Launch the Free Tier EC2 Instance

1. Log into your [AWS Management Console](https://console.aws.amazon.com/ec2/).
2. Navigate to **EC2** → Click **"Launch Instance"**.
3. Fill in the details:
   - **Name**: `gupshup-telegram-bot`
   - **Application and OS Images**: **Ubuntu Server 24.04 LTS** (or 22.04 LTS) — *Free tier eligible*.
   - **Instance Type**: 
     - Choose `t2.micro` or `t3.micro` *(whichever has the "Free tier eligible" label in your selected region)*.
   - **Key Pair**:
     - Click **"Create new key pair"**, name it `gupshup-key`, choose `.pem`, and download it to your computer.
   - **Network Settings (Firewall / Security Group)**:
     - Check **"Create security group"**.
     - Check **"Allow SSH traffic from"** → Choose **"My IP"** (or *Anywhere 0.0.0.0/0* if you have dynamic home broadband).
     - ⚠️ **Do NOT open ports 5432, 6379, or 80/443.** The bot uses Telegram long-polling (outbound connection). Keeping database ports closed makes your server 100% impenetrable from database port scanners.
   - **Configure Storage**:
     - Change storage size from `8 GiB` to **`25 GiB` or `30 GiB`** (AWS gives 30 GiB free!).
     - Volume Type: **General Purpose SSD (gp3)**.
4. Click **"Launch Instance"**.

---

### Step 3.2: Connect to Your EC2 Instance

#### Option A: Direct Browser Connection (No SSH client required!)
1. In the EC2 console, click your running instance and click **"Connect"** at the top.
2. Select **"EC2 Instance Connect"** → Click **"Connect"**. A browser terminal will open immediately.

#### Option B: From Your Terminal (Windows PowerShell / Mac / Linux)
```powershell
# In the folder where you downloaded gupshup-key.pem:
ssh -i "gupshup-key.pem" ubuntu@<YOUR-EC2-PUBLIC-IP>
```

---

### Step 3.3: Clone Repository & Run Automated Setup

Once connected to your EC2 instance, run the following commands:

```bash
# 1. Clone your repository
git clone https://github.com/<YOUR_GITHUB_USERNAME>/telegram-matchmaker.git
cd telegram-matchmaker

# 2. Make the setup script executable and run it
chmod +x scripts/setup_ec2.sh
./scripts/setup_ec2.sh
```

**What `setup_ec2.sh` does automatically:**
- Configures a **2GB Linux swapfile** (vital for 1GB RAM instances to prevent OOM errors during builds or database loads).
- Installs the latest Docker engine and `docker-compose-plugin`.
- Adds your `ubuntu` user to the `docker` group.
- Installs and enables a systemd service (`gupshup-bot.service`) so your bot automatically restarts if AWS reboots your instance.

---

### Step 3.4: Configure Environment Variables

Create your `.env` file on the EC2 server:

```bash
cp .env.example .env
nano .env
```

Set your bot token obtained from [@BotFather](https://t.me/BotFather):

```env
TELEGRAM_BOT_TOKEN="8627424809:AAEcR0SEOBjgWOOvd1DIF5zQgKU7Mdr6hUw"
ENVIRONMENT="production"
```

Save and exit: press `Ctrl + O`, then `Enter`, then `Ctrl + X`.

---

### Step 3.5: Build and Start Containers

Start all 3 services (Postgres, Redis, and Bot):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

Docker will:
1. Start `matchmaker_postgres` and wait for its healthcheck (`pg_isready`).
2. Start `matchmaker_redis` and wait for its healthcheck (`redis-cli ping`).
3. Build the bot Docker image using [Dockerfile](file:///a:/telegram-matchmaker/Dockerfile).
4. Run [entrypoint.sh](file:///a:/telegram-matchmaker/entrypoint.sh) which automatically runs `alembic upgrade head` to apply all database tables and indexes.
5. Launch the bot polling worker.

---

### Step 3.6: Verify Real-Time Logs

To see the bot live logs:

```bash
docker compose -f docker-compose.prod.yml logs -f bot
```

You should see:
```text
====================================================
🚀 Running database migrations (alembic upgrade head)...
====================================================
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 001_initial_schema
INFO  [alembic.runtime.migration] Running upgrade ... -> create_sessions_and_moderation_tables
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

## 4. Useful Operational Commands

| Action | Command |
|---|---|
| **View Bot Logs** | `docker compose -f docker-compose.prod.yml logs -f bot` |
| **Check Container Status** | `docker compose -f docker-compose.prod.yml ps` |
| **Restart Bot** | `docker compose -f docker-compose.prod.yml restart bot` |
| **Stop Everything** | `docker compose -f docker-compose.prod.yml down` |
| **Update to Newest Code** | `git pull && docker compose -f docker-compose.prod.yml up -d --build` |
| **Inspect PostgreSQL Database** | `docker exec -it matchmaker_postgres psql -U matchmaker_user -d matchmaker_db` |
| **Inspect Redis** | `docker exec -it matchmaker_redis redis-cli` |

---

## 5. Set Up AWS Zero-Bill Safety Alarm ($0.01 Alert)

To guarantee peace of mind that you will never be charged:

1. In AWS Console search bar, type **"Budgets"** and select **AWS Budgets**.
2. Click **"Create a budget"**.
3. Choose **"Zero spend budget"** (or Cost Budget with Amount = `$0.01`).
4. Enter your email address.
5. Click **"Create budget"**.

AWS will immediately email you if your account ever generates even $0.01 in charges.
