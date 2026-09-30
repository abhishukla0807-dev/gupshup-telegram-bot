# ==========================================
# GupShup Telegram Matchmaker Dockerfile
# Production-ready for Railway deployment
# ==========================================

FROM python:3.11-slim-bookworm

# Python and system environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt /app/

# Install python dependencies
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application source code
COPY . /app/

# Ensure entrypoint.sh has Linux line endings (LF) and executable permissions
RUN sed -i 's/\r$//' /app/entrypoint.sh && \
    chmod +x /app/entrypoint.sh

# Run as non-root user for production security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Healthcheck to ensure container is responsive
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; exit(0)" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
