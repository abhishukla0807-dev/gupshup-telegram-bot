#!/usr/bin/env bash
# ==============================================================================
# GupShup Bot - 1-Click AWS CLI Automated Provisioner
# Creates Security Group, Key Pair, and Launches Amazon Linux 2023 t2.micro
# ==============================================================================

set -e

KEY_NAME="gupshup-key"
SG_NAME="gupshup-bot-sg"
INSTANCE_TYPE="t2.micro"

echo "=========================================================="
echo "☁️ 1. Checking AWS CLI configuration..."
echo "=========================================================="
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI is not installed. Please install it first:"
    echo "   https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

AWS_REGION=$(aws configure get region || echo "us-east-1")
echo "Using AWS Region: ${AWS_REGION}"

echo "=========================================================="
echo "🔑 2. Checking/Creating EC2 Key Pair (${KEY_NAME})..."
echo "=========================================================="
if ! aws ec2 describe-key-pairs --key-names "${KEY_NAME}" &>/dev/null; then
    echo "Creating new key pair: ${KEY_NAME}.pem"
    aws ec2 create-key-pair --key-name "${KEY_NAME}" --query "KeyMaterial" --output text > "${KEY_NAME}.pem"
    chmod 400 "${KEY_NAME}.pem"
    echo "✅ Key saved to $(pwd)/${KEY_NAME}.pem"
else
    echo "ℹ️ Key pair '${KEY_NAME}' already exists in AWS."
fi

echo "=========================================================="
echo "🛡️ 3. Configuring Security Group (${SG_NAME})..."
echo "=========================================================="
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)

SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=${SG_NAME}" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null || true)

if [ -z "$SG_ID" ] || [ "$SG_ID" = "None" ]; then
    echo "Creating security group in VPC: ${VPC_ID}"
    SG_ID=$(aws ec2 create-security-group \
        --group-name "${SG_NAME}" \
        --description "Security group for GupShup Telegram Bot (SSH only)" \
        --vpc-id "${VPC_ID}" \
        --query "GroupId" --output text)
    echo "✅ Created security group: ${SG_ID}"
else
    echo "ℹ️ Using existing security group: ${SG_ID}"
fi

MY_IP=$(curl -s https://checkip.amazonaws.com || echo "0.0.0.0")
echo "Authorizing SSH (Port 22) for your IP: ${MY_IP}/32"
aws ec2 authorize-security-group-ingress \
    --group-id "${SG_ID}" \
    --protocol tcp \
    --port 22 \
    --cidr "${MY_IP}/32" 2>/dev/null || echo "ℹ️ SSH rule already present."

echo "=========================================================="
echo "🔍 4. Fetching latest Amazon Linux 2023 Free Tier AMI..."
echo "=========================================================="
AMI_ID=$(aws ssm get-parameter \
    --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" \
    --query "Parameter.Value" --output text)
echo "Found AMI: ${AMI_ID}"

echo "=========================================================="
echo "🚀 5. Launching t2.micro Instance..."
echo "=========================================================="
INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "${AMI_ID}" \
    --instance-type "${INSTANCE_TYPE}" \
    --key-name "${KEY_NAME}" \
    --security-group-ids "${SG_ID}" \
    --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=gupshup-bot}]' \
    --query "Instances[0].InstanceId" --output text)

echo "✅ Instance launched: ${INSTANCE_ID}"
echo "⏳ Waiting for instance to receive public IP..."

aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}"

PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "${INSTANCE_ID}" \
    --query "Reservations[0].Instances[0].PublicIpAddress" --output text)

echo "=========================================================="
echo "🎉 EC2 Instance is LIVE!"
echo "=========================================================="
echo "Public IP: ${PUBLIC_IP}"
echo ""
echo "Connect with SSH:"
echo "  ssh -i \"${KEY_NAME}.pem\" ec2-user@${PUBLIC_IP}"
echo ""
echo "Next inside EC2:"
echo "  git clone https://github.com/abhishukla0807-dev/gupshup-telegram-bot.git"
echo "  cd gupshup-telegram-bot"
echo "  chmod +x scripts/setup_ec2.sh && ./scripts/setup_ec2.sh"
echo "  nano .env  # set TELEGRAM_BOT_TOKEN"
echo "  docker compose up -d --build"
echo "=========================================================="
