FROM python:3.13-slim

# Install tini for proper init and signal handling
# System dependencies for Pillow, headless OpenCV, and Intel GPU compute runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        tini \
        ocl-icd-libopencl1 \
        wget \
    && rm -rf /var/lib/apt/lists/*

# Intel GPU compute runtime — install .deb packages from GitHub releases
# Provides OpenCL ICD + Level Zero for OpenVINO GPU inference via /dev/dri
# https://github.com/intel/compute-runtime/releases
ARG IGC_VERSION=2.30.1
ARG IGC_BUILD=20950
ARG NEO_VERSION=26.09.37435.1
ARG GMMLIB_VERSION=22.9.0
RUN mkdir -p /tmp/neo && cd /tmp/neo && \
    wget -q https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC_VERSION}/intel-igc-core-2_${IGC_VERSION}+${IGC_BUILD}_amd64.deb && \
    wget -q https://github.com/intel/intel-graphics-compiler/releases/download/v${IGC_VERSION}/intel-igc-opencl-2_${IGC_VERSION}+${IGC_BUILD}_amd64.deb && \
    wget -q https://github.com/intel/compute-runtime/releases/download/${NEO_VERSION}/intel-opencl-icd_${NEO_VERSION}-0_amd64.deb && \
    wget -q https://github.com/intel/compute-runtime/releases/download/${NEO_VERSION}/intel-ocloc_${NEO_VERSION}-0_amd64.deb && \
    wget -q https://github.com/intel/compute-runtime/releases/download/${NEO_VERSION}/libigdgmm12_${GMMLIB_VERSION}_amd64.deb && \
    wget -q https://github.com/intel/compute-runtime/releases/download/${NEO_VERSION}/libze-intel-gpu1_${NEO_VERSION}-0_amd64.deb && \
    dpkg -i *.deb && \
    rm -rf /tmp/neo && \
    apt-get purge -y wget && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

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
