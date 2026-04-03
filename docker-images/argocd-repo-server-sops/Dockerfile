 # ArgoCD Repo Server with SOPS support
# Based on official ArgoCD repo-server image
ARG ARGOCD_VERSION=v3.3.6

FROM quay.io/argoproj/argocd:${ARGOCD_VERSION}

# Switch to root to install packages
USER root

# Install dependencies
RUN apt-get update && \
    apt-get install -y \
    curl \
    gpg \
    wget && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install SOPS
ARG SOPS_VERSION=3.11.0
RUN curl -Lo /usr/local/bin/sops https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64 && \
    chmod +x /usr/local/bin/sops

# Install age
ARG AGE_VERSION=1.3.1
RUN curl -Lo age.tar.gz https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-amd64.tar.gz && \
    tar -xzf age.tar.gz && \
    mv age/age /usr/local/bin/ && \
    mv age/age-keygen /usr/local/bin/ && \
    chmod +x /usr/local/bin/age /usr/local/bin/age-keygen && \
    rm -rf age age.tar.gz

# Install helm-secrets plugin
ARG HELM_SECRETS_VERSION=4.7.4
ENV HELM_PLUGINS="/home/argocd/.local/share/helm/plugins"
RUN mkdir -p ${HELM_PLUGINS} && \
    curl -Lo helm-secrets.tar.gz https://github.com/jkroepke/helm-secrets/releases/download/v${HELM_SECRETS_VERSION}/helm-secrets.tar.gz && \
    tar -C ${HELM_PLUGINS} -xzf helm-secrets.tar.gz && \
    rm helm-secrets.tar.gz

# Set up SOPS backend environment
ENV HELM_SECRETS_BACKEND="sops" \
    HELM_SECRETS_SOPS_PATH="/usr/local/bin/sops" \
    HELM_SECRETS_KUBECTL_PATH="/usr/local/bin/kubectl" \
    HELM_SECRETS_CURL_PATH="/usr/bin/curl"

# Fix permissions for argocd user (UID 999)
RUN chown -R 999:999 ${HELM_PLUGINS} && \
    chown 999:999 /usr/local/bin/sops /usr/local/bin/age /usr/local/bin/age-keygen

# Switch back to argocd user (UID 999)
USER 999

# Verify installations
RUN sops --version && \
    age --version && \
    helm plugin list
