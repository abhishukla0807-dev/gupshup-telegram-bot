#!/usr/bin/env bash
# ==============================================================================
# GupShup Bot - AWS EC2 Free Tier Automated Setup Script
# Supports: Amazon Linux 2023 (dnf / ec2-user) & Ubuntu 22.04/24.04 (apt / ubuntu)
# ==============================================================================

set -e

echo "=========================================================="
echo "🚀 1. Detecting OS & Installing Dependencies..."
echo "=========================================================="

CURRENT_USER=$(whoami)

if command -v dnf &> /dev/null; then
    echo "Detected Amazon Linux 2023 / RHEL family (dnf)"
    sudo dnf update -y
    sudo dnf install -y docker git curl htop

    # Enable and start Docker daemon
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$CURRENT_USER" || true
    sudo usermod -aG docker ec2-user 2>/dev/null || true

    # Install Docker Compose CLI plugin
    if ! docker compose version &> /dev/null; then
        echo "Installing Docker Compose CLI plugin..."
        sudo dnf install -y docker-compose-plugin || {
            CLI_PLUGINS_DIR="/usr/local/lib/docker/cli-plugins"
            sudo mkdir -p "$CLI_PLUGINS_DIR"
            ARCH=$(uname -m)
            [ "$ARCH" = "x86_64" ] && ARCH="x86_64" || ARCH="aarch64"
            sudo curl -sSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}" -o "$CLI_PLUGINS_DIR/docker-compose"
            sudo chmod +x "$CLI_PLUGINS_DIR/docker-compose"
        }
    fi
elif command -v apt-get &> /dev/null; then
    echo "Detected Ubuntu / Debian family (apt)"
    sudo apt-get update -y
    sudo apt-get install -y ca-certificates curl gnupg git htop

    if ! command -v docker &> /dev/null; then
        sudo install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
        sudo chmod a+r /etc/apt/keyrings/docker.gpg

        echo \
          "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
          $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

        sudo apt-get update -y
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi

    sudo systemctl enable --now docker
    sudo usermod -aG docker "$CURRENT_USER" || true
    sudo usermod -aG docker ubuntu 2>/dev/null || true
fi

echo "=========================================================="
echo "🧠 2. Configuring 2GB Swap Memory for AWS Free Tier..."
echo "=========================================================="
# AWS t2.micro comes with 1GB RAM. Adding 2GB swap prevents OOM-killer during builds.
if [ ! -f /swapfile ]; then
    echo "Creating 2GB swapfile..."
    sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    sudo sysctl vm.swappiness=20 || true
    echo "✅ 2GB Swap space enabled."
else
    echo "ℹ️ /swapfile already exists. Skipping."
fi

echo "=========================================================="
echo "⚙️ 3. Configuring Auto-Restart Systemd Service..."
echo "=========================================================="
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sudo bash -c "cat <<EOF > /etc/systemd/system/gupshup-bot.service
[Unit]
Description=GupShup Matchmaker Bot Application
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/docker compose up -d --build
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable gupshup-bot.service
echo "✅ Auto-restart service (gupshup-bot.service) registered."

echo "=========================================================="
echo "🎉 EC2 Setup Completed Successfully!"
echo "=========================================================="
echo ""
echo "Quick Start Commands:"
echo "1. Configure your token in .env:"
echo "     cat > .env << 'EOF'"
echo "     TELEGRAM_BOT_TOKEN=your_token_here"
echo "     ENVIRONMENT=production"
echo "     EOF"
echo ""
echo "2. Launch all services (PostgreSQL, Redis, Bot):"
echo "     docker compose up -d --build"
echo ""
echo "3. View real-time logs:"
echo "     docker compose logs -f gupshup-bot"
echo ""
