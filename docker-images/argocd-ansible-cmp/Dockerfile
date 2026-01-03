FROM ubuntu:22.04

# Install dependencies
RUN apt-get update && \
    apt-get install -y \
    python3 \
    python3-pip \
    git \
    curl \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Ansible
RUN pip3 install --no-cache-dir \
    ansible \
    jmespath \
    kubernetes

# Create argocd user and directories
RUN groupadd -g 999 argocd && \
    useradd -r -u 999 -g argocd argocd && \
    mkdir -p /home/argocd && \
    chown -R 999:999 /home/argocd

# Create output directory for rendered manifests
RUN mkdir -p /tmp/ansible-output && \
    chown -R 999:999 /tmp/ansible-output

# Switch to argocd user
USER 999

WORKDIR /home/argocd

# Set environment variables for Ansible
ENV ANSIBLE_LOCALHOST_WARNING=False \
    ANSIBLE_INVENTORY_UNPARSED_WARNING=False \
    ANSIBLE_HOST_KEY_CHECKING=False
