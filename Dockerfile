# ==============================================================================
# GupShup Telegram Matchmaker Dockerfile
# Optimized for AWS EC2 Free Tier (Amazon Linux 2023 / Ubuntu)
# ==============================================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# Copy dependency specifications first for Docker layer caching
COPY requirements.txt .

# Install python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run entrypoint script or root bot.py (runs alembic migrations + starts bot)
CMD ["python", "bot.py"]
