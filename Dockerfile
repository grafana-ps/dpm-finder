# Multi-stage build for dpm-finder
# Stage 1: Build dependencies
FROM python:3.12-slim as builder

# Set working directory
WORKDIR /app

# Install system dependencies for building packages
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies in a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Production image
FROM python:3.12-slim

# Create non-root user
RUN useradd --create-home --shell /bin/bash dpmfinder

# Set working directory
WORKDIR /app

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application files
COPY dpm-finder.py .
COPY README.md .

# Set up environment
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Change ownership to non-root user
RUN chown -R dpmfinder:dpmfinder /app

# Switch to non-root user
USER dpmfinder

# Expose the default port
EXPOSE 9966

# Add health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:9966/metrics', timeout=5)" || exit 1

# Set entrypoint
ENTRYPOINT ["python", "dpm-finder.py"]

# Default command (can be overridden)
CMD ["--exporter"] 