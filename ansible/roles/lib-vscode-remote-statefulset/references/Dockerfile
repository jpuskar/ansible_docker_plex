# VS Code Remote Development Container with SSH Server
# Based on Ubuntu 24.04 (build with --platform linux/arm64 for NVIDIA Grace/GB10)
FROM ubuntu:24.04

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    USER=vscode \
    UID=1000 \
    GID=1000 \
    HOME=/home/vscode \
    WORKSPACE=/workspace \
    SSH_PORT=2222

# Install essential packages
RUN apt-get update && apt-get install -y \
    apt-transport-https \
    bash-completion \
    build-essential \
    ca-certificates \
    curl \
    fish \
    git \
    gnupg \
    htop \
    jq \
    lsb-release \
    man-db \
    nano \
    openssh-server \
    python3 \
    python3-pip \
    python3-venv \
    software-properties-common \
    tmux \
    unzip \
    vim \
    wget \
    zip \
    zsh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# GPU/ML development notes:
# - nvidia-smi provided by nvidia container runtime (runtimeClassName: nvidia)
# - PyTorch/HuggingFace: install via pip (wheels include CUDA libraries)
#   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
#   pip install transformers accelerate

# Create vscode user (handle existing UID/GID 1000 from base image)
RUN set -e && \
    # Ensure group exists
    if getent group ${GID} >/dev/null; then \
        EXISTING_GROUP=$(getent group ${GID} | python3 -c "import sys; print(sys.stdin.read().split(':')[0])"); \
        if [ "$EXISTING_GROUP" != "${USER}" ]; then \
            groupmod -n ${USER} $EXISTING_GROUP; \
        fi; \
    else \
        groupadd -g ${GID} ${USER}; \
    fi && \
    # Ensure user exists
    if getent passwd ${UID} >/dev/null; then \
        EXISTING_USER=$(getent passwd ${UID} | python3 -c "import sys; print(sys.stdin.read().split(':')[0])"); \
        if [ "$EXISTING_USER" != "${USER}" ]; then \
            usermod -l ${USER} -d ${HOME} -m -g ${GID} $EXISTING_USER; \
        else \
            usermod -g ${GID} ${USER}; \
        fi; \
    else \
        useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USER}; \
    fi && \
    mkdir -p ${WORKSPACE} && \
    chown -R ${UID}:${GID} ${WORKSPACE}

# Configure SSH - run as non-root user
RUN mkdir -p ${HOME}/.ssh && \
    chmod 700 ${HOME}/.ssh && \
    mkdir -p ${HOME}/.ssh/sshd && \
    chown -R ${USER}:${USER} ${HOME}/.ssh

# Copy entrypoint script
COPY start.sh /start.sh
RUN chmod +x /start.sh

# Switch to vscode user
USER ${USER}
WORKDIR ${WORKSPACE}

# Expose SSH port
EXPOSE ${SSH_PORT}

# Set entrypoint
ENTRYPOINT ["/start.sh"]
CMD ["/bin/bash"]
