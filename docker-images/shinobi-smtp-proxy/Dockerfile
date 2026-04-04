FROM python:3.13-slim

# Install tini for proper init and signal handling
# System dependencies for Pillow and headless OpenCV
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies (CPU-only PyTorch to avoid ~1.5GB of CUDA stubs)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Download YOLO model during build to avoid downloading on every container start
# This caches the model in the image
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# Copy application source
COPY src/ ./src/

# Create non-root user for security
RUN useradd -r -u 1001 -m -s /bin/bash smtpproxy && \
    chown -R smtpproxy:smtpproxy /app

# Create config directory (will be mounted as volume)
RUN mkdir -p /config && chown smtpproxy:smtpproxy /config

# Switch to non-root user
USER smtpproxy

# Set Python to run in unbuffered mode for better logging
ENV PYTHONUNBUFFERED=1

# Expose SMTP proxy port
EXPOSE 2525

# Health check - verify the health endpoint responds
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Use tini as init system for proper signal handling
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the SMTP proxy
CMD ["python", "-u", "src/app.py"]
