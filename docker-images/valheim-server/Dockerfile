# Rootless Valheim dedicated server
# Runs as UID 10000 — never root.
# Includes valheim-logfilter from community-valheim-tools for cleaner logs.

# ── Stage 1: Build valheim-logfilter ──────────────────────────────────
FROM golang:1.24-bookworm AS logfilter-build
WORKDIR /build
RUN git clone --depth 1 https://github.com/community-valheim-tools/valheim-server-docker.git \
    && cd valheim-server-docker/valheim-logfilter \
    && go build -ldflags="-s -w" -o /valheim-logfilter .

# ── Stage 2: Final image ─────────────────────────────────────────────
FROM steamcmd/steamcmd:ubuntu

ENV VALHEIM_APP_ID="896660" \
    SERVER_DIR="/server" \
    CONFIG_DIR="/config"

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        bash \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 10000 valheim \
    && useradd -u 10000 -g 10000 -m -d /home/valheim -s /bin/bash valheim

# Create directories
RUN mkdir -p "${SERVER_DIR}" "${CONFIG_DIR}" /home/valheim/.steam \
    && chown -R 10000:10000 "${SERVER_DIR}" "${CONFIG_DIR}" /home/valheim

# Download Valheim dedicated server as root (steamcmd needs its own dirs),
# then hand ownership to the valheim user.
RUN steamcmd \
        +force_install_dir "${SERVER_DIR}" \
        +login anonymous \
        +app_update ${VALHEIM_APP_ID} validate \
        +quit \
    && ln -s "${SERVER_DIR}/linux64" /home/valheim/.steam/sdk64 \
    && chown -R 10000:10000 "${SERVER_DIR}" /home/valheim

# Symlink config into the path Valheim expects for world data
RUN ln -s "${CONFIG_DIR}" /home/valheim/.config \
    || true

# Copy valheim-logfilter from build stage
COPY --from=logfilter-build /valheim-logfilter /usr/local/bin/valheim-logfilter
RUN chmod 755 /usr/local/bin/valheim-logfilter

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 2456/udp 2457/udp

WORKDIR /server
USER 10000

ENTRYPOINT ["tini", "-g", "--"]
CMD ["/start.sh"]
