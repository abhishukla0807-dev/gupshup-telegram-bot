# ==============================================================================
# GupShup Bot - 1-Click AWS CLI Automated Provisioner (PowerShell)
# Creates Security Group, Key Pair, and Launches Amazon Linux 2023 t2.micro
# ==============================================================================

$ErrorActionPreference = "Stop"

$KeyName = "gupshup-key"
$SgName = "gupshup-bot-sg"
$InstanceType = "t2.micro"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[*] 1. Checking AWS CLI configuration..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    if (Test-Path "C:\Program Files\Amazon\AWSCLIV2\aws.exe") {
        $env:Path += ";C:\Program Files\Amazon\AWSCLIV2"
    } else {
        Write-Error "AWS CLI is not installed. Please install it from: https://aws.amazon.com/cli/"
        exit 1
    }
}

$AwsRegion = & aws configure get region
if (-not $AwsRegion) { $AwsRegion = "ap-south-1" }
Write-Host "Using AWS Region: $AwsRegion" -ForegroundColor Green

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[*] 2. Checking or Creating EC2 Key Pair ($KeyName)..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$KeyExists = & aws ec2 describe-key-pairs --key-names $KeyName 2>$null
if (-not $KeyExists) {
    Write-Host "Creating new key pair: $KeyName.pem"
    $KeyMaterial = & aws ec2 create-key-pair --key-name $KeyName --query "KeyMaterial" --output text
    Set-Content -Path "$KeyName.pem" -Value $KeyMaterial -Encoding Ascii
    Write-Host "[OK] Key saved to: $KeyName.pem" -ForegroundColor Green
} else {
    Write-Host "[INFO] Key pair $KeyName already exists in AWS." -ForegroundColor Yellow
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[*] 3. Configuring Security Group ($SgName)..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$VpcId = & aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text

$SgId = & aws ec2 describe-security-groups --filters "Name=group-name,Values=$SgName" --query "SecurityGroups[0].GroupId" --output text 2>$null

if (-not $SgId -or $SgId -eq "None") {
    Write-Host "Creating security group in VPC: $VpcId"
    $SgId = & aws ec2 create-security-group --group-name $SgName --description "Security group for GupShup Telegram Bot (SSH only)" --vpc-id $VpcId --query "GroupId" --output text
    Write-Host "[OK] Created security group: $SgId" -ForegroundColor Green
} else {
    Write-Host "[INFO] Using existing security group: $SgId" -ForegroundColor Yellow
}

$MyIp = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com").Trim()
Write-Host "Authorizing SSH (Port 22) for your IP: $MyIp/32"
& aws ec2 authorize-security-group-ingress --group-id $SgId --protocol tcp --port 22 --cidr "$MyIp/32" 2>$null

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[*] 4. Fetching latest Amazon Linux 2023 Free Tier AMI..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$AmiId = & aws ssm get-parameter --name "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64" --query "Parameter.Value" --output text

Write-Host "Found AMI: $AmiId" -ForegroundColor Green

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[*] 5. Launching t2.micro Instance..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$runArgs = @(
    "ec2", "run-instances",
    "--image-id", $AmiId,
    "--instance-type", $InstanceType,
    "--key-name", $KeyName,
    "--security-group-ids", $SgId,
    "--query", "Instances[0].InstanceId",
    "--output", "text"
)

$InstanceId = & aws @runArgs
Write-Host "[OK] Instance launched: $InstanceId" -ForegroundColor Green

& aws ec2 create-tags --resources $InstanceId --tags Key=Name,Value=gupshup-bot

Write-Host "Waiting for instance to receive public IP (10-15 seconds)..." -ForegroundColor Yellow
& aws ec2 wait instance-running --instance-ids $InstanceId

$PublicIp = & aws ec2 describe-instances --instance-ids $InstanceId --query "Reservations[0].Instances[0].PublicIpAddress" --output text

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "[SUCCESS] EC2 Instance is LIVE!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Public IP: $PublicIp"
Write-Host ""
Write-Host "Connect with SSH:"
Write-Host "  ssh -i $KeyName.pem ec2-user@$PublicIp"
Write-Host ""
Write-Host "Next inside EC2:"
Write-Host "  git clone https://github.com/abhishukla0807-dev/gupshup-telegram-bot.git"
Write-Host "  cd gupshup-telegram-bot"
Write-Host "  chmod +x scripts/setup_ec2.sh && ./scripts/setup_ec2.sh"
Write-Host "  nano .env"
Write-Host "  docker compose up -d --build"
Write-Host "==========================================================" -ForegroundColor Green
