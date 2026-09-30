#!/usr/bin/env bash
# ==============================================================================
# GupShup Bot - AWS EC2 Free Tier Automated Setup Script
# Works on Ubuntu 22.04 LTS / 24.04 LTS (t2.micro / t3.micro)
# ==============================================================================

set -e

echo "=========================================================="
echo "🚀 1. Updating System Packages..."
echo "=========================================================="
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release git htop

echo "=========================================================="
echo "🧠 2. Configuring 2GB Swap Memory for AWS Free Tier..."
echo "=========================================================="
# t2.micro / t3.micro comes with 1GB RAM. Adding a 2GB swapfile guarantees
# that Docker builds and database operations will never trigger Linux OOM Killer.
if [ ! -f /swapfile ]; then
    echo "Creating 2GB swapfile..."
    sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    sudo sysctl vm.swappiness=20
    echo 'vm.swappiness=20' | sudo tee -a /etc/sysctl.conf
    echo "✅ 2GB Swap memory activated successfully."
else
    echo "ℹ️ /swapfile already exists. Skipping."
fi

echo "=========================================================="
echo "🐳 3. Installing Docker & Docker Compose Plugin..."
echo "=========================================================="
if ! command -v docker &> /dev/null; then
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -y
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Add current user to docker group
    sudo usermod -aG docker "$USER"
    sudo systemctl enable docker
    sudo systemctl start docker
    echo "✅ Docker installed successfully."
else
    echo "ℹ️ Docker is already installed."
fi

echo "=========================================================="
echo "⚙️ 4. Setting up Auto-Restart Systemd Service..."
echo "=========================================================="
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sudo bash -c "cat <<EOF > /etc/systemd/system/gupshup-bot.service
[Unit]
Description=GupShup Matchmaker Bot Docker Compose Application
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d --build
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable gupshup-bot.service
echo "✅ Systemd service created: gupshup-bot.service (Enabled for auto-start on reboot)"

echo "=========================================================="
echo "🎉 AWS EC2 Setup Complete!"
echo "=========================================================="
echo ""
echo "Next Steps:"
echo "1. Create/edit your .env file in ${REPO_DIR}:"
echo "     nano .env"
echo "   (Set TELEGRAM_BOT_TOKEN, etc.)"
echo ""
echo "2. Start your bot containers:"
echo "     docker compose -f docker-compose.prod.yml up -d --build"
echo ""
echo "3. View real-time logs:"
echo "     docker compose -f docker-compose.prod.yml logs -f bot"
echo ""
