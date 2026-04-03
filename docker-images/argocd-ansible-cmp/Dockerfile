FROM ubuntu:22.04

# Tool versions (matching ArgoCD v3.3.6)
ENV HELM_VERSION=3.19.4 \
    KUBECTL_VERSION=1.34.1 \
    KUSTOMIZE_VERSION=5.8.1 \
    YQ_VERSION=4.47.1

# Install dependencies
RUN apt-get update && \
    apt-get install -y \
    coreutils \
    curl \
    git \
    jq \
    less \
    python3 \
    python3-pip \
    tar \
    unzip \
    wget \
    xz-utils \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Ansible
RUN pip3 install --no-cache-dir \
    ansible \
    ansible-core \
    dnspython \
    jmespath \
    kubernetes \
    netaddr \
    python-dateutil \
    requests \
    yamllint

# Install Helm
RUN curl -fsSL https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz | tar -xzv -C /tmp && \
    mv /tmp/linux-amd64/helm /usr/local/bin/helm && \
    rm -rf /tmp/linux-amd64 && \
    helm version

# Install kubectl
RUN curl -fsSLO https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl && \
    chmod +x kubectl && \
    mv kubectl /usr/local/bin/kubectl && \
    kubectl version --client

# Install kustomize
RUN curl -fsSL https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv${KUSTOMIZE_VERSION}/kustomize_v${KUSTOMIZE_VERSION}_linux_amd64.tar.gz \
      | tar -xzv -C /tmp \
    && mv /tmp/kustomize /usr/local/bin/kustomize \
    && kustomize version

# Install yq
RUN curl -fsSL https://github.com/mikefarah/yq/releases/download/v${YQ_VERSION}/yq_linux_amd64 -o /usr/local/bin/yq && \
    chmod +x /usr/local/bin/yq && \
    yq --version

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
    ANSIBLE_INVENTORY_UNPARSED_WARNING=False

# Run argocd-cmp-server
CMD ["/var/run/argocd/argocd-cmp-server"]
