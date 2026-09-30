# ☁️ AWS Free Tier Deployment Guide (Amazon Linux 2023 & EC2 + Docker)

Complete step-by-step guide to deploy the **GupShup Telegram Matchmaker Bot** on AWS Free Tier using an EC2 instance with Docker.

---

## 1. AWS Free Tier Limits & Cost Breakdown

| Resource | AWS Free Tier Limit | Bot Consumption | Estimated Monthly Cost |
|---|---|---|---|
| **EC2 `t2.micro`** | **750 hours / month** | 1 instance running 24/7 (~744 hours) | **$0.00 (100% FREE)** |
| **EBS Storage** | **30 GB / month** (gp3/gp2 SSD) | ~10–20 GB | **$0.00 (100% FREE)** |
| **Data Transfer Out** | **100 GB / month** | < 500 MB (polling) | **$0.00 (100% FREE)** |
| **CloudWatch** | 10 metrics, 10 alarms | Basic monitoring | **$0.00 (100% FREE)** |

---

## 2. Step 1 — Launch a Free Tier EC2 Instance

Open the [EC2 Launch Instance page](https://console.aws.amazon.com/ec2/v2/home#LaunchInstances).

Fill in the following:

| Field | Value |
|---|---|
| **Name** | `gupshup-bot` |
| **AMI** | **Amazon Linux 2023** (Free Tier eligible) |
| **Instance type** | `t2.micro` (Free Tier — 750 hrs/month) |
| **Key pair** | Create new → name it `gupshup-key` → download the `.pem` file |
| **Storage** | `20 GB` or `30 GB` gp3 (Free Tier covers up to 30 GB!) |

---

## 3. Step 2 — Configure the Security Group

During launch, under **Network settings → Edit**, create a new security group named `gupshup-bot-sg` with these inbound rules only:

| Type | Protocol | Port | Source | Purpose |
|---|---|---|---|---|
| **SSH** | TCP | `22` | **My IP** (Your IP only) | Admin access |

> [!IMPORTANT]
> **No other inbound ports are needed.** Your bot uses long-polling — it reaches out to Telegram (`api.telegram.org`), Telegram never calls back in. Do **NOT** open port 80/443/5432/6379 to the public internet!
>
> **Outbound rules**: Leave default (allow all outbound traffic).

Click **Launch instance**.

---

## 4. Step 3 — Connect to Your EC2 Instance

On your local machine, open your terminal / PowerShell in the folder where your key was downloaded:

```bash
# Fix key permissions (Linux/macOS)
chmod 400 gupshup-key.pem

# SSH into the instance:
ssh -i "gupshup-key.pem" ec2-user@<YOUR_PUBLIC_IP>
```
*(On Windows, you can use built-in Windows PowerShell `ssh` or PuTTY).*

---

## 5. Step 4 — Install Docker, Git & Dependencies

Run these commands after SSH-ing in:

```bash
# Update packages
sudo dnf update -y

# Install Docker, Git, and utilities
sudo dnf install -y docker git

# Start Docker and enable it on boot
sudo systemctl enable --now docker

# Add ec2-user to docker group
sudo usermod -aG docker ec2-user

# Apply group changes without logging out
newgrp docker

# Verify Docker
docker --version
```

---

## 6. Step 5 — Clone Your Repo & Run Automated Setup

```bash
# Clone your repository
git clone https://github.com/abhishukla0807-dev/gupshup-telegram-bot.git
cd gupshup-telegram-bot

# Run the setup script (configures 2GB swap space to prevent OOM on 1GB RAM micro instances & registers auto-start systemd service)
chmod +x scripts/setup_ec2.sh
./scripts/setup_ec2.sh
```

---

## 7. Step 6 — Set Up Environment Variables Securely

Never hardcode tokens in your code or Dockerfile. Create a `.env` file on the server:

```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN="your_telegram_bot_token_here"
ENVIRONMENT="production"
EOF

# Restrict permissions so only your user can read it
chmod 600 .env
```

---

## 8. Step 7 — Build and Run the Application

We provide a production [docker-compose.yml](file:///a:/telegram-matchmaker/docker-compose.yml) that starts **PostgreSQL 15**, **Redis 7**, and your **GupShup Bot** together in an isolated private Docker network:

```bash
docker compose up -d --build
```

The container automatically:
1. Waits for PostgreSQL and Redis health checks to pass.
2. Applies all database migrations automatically via `alembic upgrade head`.
3. Launches the bot and matchmaking engine with `--restart unless-stopped`.

---

## 9. Step 8 — Verify the Bot is Running

```bash
# Check running containers
docker ps

# Tail live bot logs
docker compose logs -f gupshup-bot
```

You should see:
```text
====================================================
🚀 Applying database migrations (alembic upgrade head)...
====================================================
✅ Database schema is up to date.
INFO  | __main__ | Starting GupShup matchmaker in production mode...
INFO  | app.infrastructure.redis | Redis connection verified: ping response=True
INFO  | app.core.matching.worker | MatchmakingWorker started with tick interval 1.00s
INFO  | aiogram.dispatcher | Run polling for bot @GupShupNowBot
```

---

## 10. Step 9 — 1-Click Deployment via AWS CLI

If you prefer to deploy automatically from your computer using the **AWS CLI** without clicking through the AWS Console:

### From Windows PowerShell:
```powershell
.\scripts\deploy_aws_cli.ps1
```

### From Linux / macOS / WSL:
```bash
chmod +x scripts/deploy_aws_cli.sh
./scripts/deploy_aws_cli.sh
```

This automated script will:
1. Verify your AWS CLI credentials.
2. Automatically create `gupshup-key.pem`.
3. Create the `gupshup-bot-sg` security group with Port 22 locked to your IP.
4. Locate the latest Amazon Linux 2023 Free Tier AMI.
5. Launch the `t2.micro` instance with a 20 GB gp3 SSD.
6. Print the public IP and ready-to-run SSH command!

---

## 11. Step 10 — Updating the Bot (Deploy New Code)

Whenever you push updates to GitHub:

```bash
cd ~/gupshup-telegram-bot
git pull origin main
docker compose up -d --build
```

---

## 12. Quick Reference Commands

```bash
docker compose logs -f gupshup-bot  # View live logs
docker compose restart gupshup-bot # Restart bot
docker compose ps                  # Check status
docker compose down                # Stop all containers
docker stats                       # Real-time CPU & Memory usage
```
