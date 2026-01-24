# Fingent Dockerfile
# Multi-stage build for smaller image

# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml .
COPY fingent/ fingent/

# Install dependencies
RUN pip install --no-cache-dir .

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY fingent/ fingent/
COPY config/ config/

# Create data directory
RUN mkdir -p data/logs

# Set environment
ENV FINGENT_ENV=aws
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "from fingent.core.config import get_settings; get_settings()"

# Default command
CMD ["python", "-m", "fingent.cli.main", "--scheduled"]
