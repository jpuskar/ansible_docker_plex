FROM python:3.13-slim

# Install tini for proper init and signal handling
# System dependencies for Pillow, headless OpenCV, and Intel GPU compute runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        tini \
        # Intel GPU compute runtime for OpenVINO GPU inference
        # Provides libze_loader, libigdgmm, libigc — needed for /dev/dri access
        gnupg \
        curl \
    && curl -fsSL https://repositories.intel.com/gpu/intel-graphics.key | \
       gpg --dearmor -o /usr/share/keyrings/intel-graphics.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu-24.04/lts/2350 unified main" \
       > /etc/apt/sources.list.d/intel-gpu.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        intel-opencl-icd \
        level-zero \
    && apt-get purge -y gnupg curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies (CPU-only PyTorch to avoid ~1.5GB of CUDA stubs)
# Install OpenVINO for Intel GPU inference acceleration
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir openvino

# Download YOLO model and export to OpenVINO IR format during build
# The exported model is used at runtime for GPU-accelerated inference
RUN python -c "from ultralytics import YOLO; YOLO('yolov8l.pt')" && \
    python -c "from ultralytics import YOLO; YOLO('yolov8l.pt').export(format='openvino', imgsz=640, half=False)"

# Copy application source
COPY src/ ./src/

# Create non-root user in 'render' group for Intel GPU /dev/dri access
# GID 109 is the standard render group on Debian/Ubuntu
RUN groupadd -g 109 render 2>/dev/null || true && \
    useradd -r -u 1001 -m -s /bin/bash -G render smtpproxy && \
    chown -R smtpproxy:smtpproxy /app && \
    chown -R smtpproxy:smtpproxy /tmp/Ultralytics

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
