# Rootless tModLoader dedicated server for k8s
# Based on JACOBSMILE/tmodloader1.4, adapted for rootless operation.
# Runs as UID 10000 — never root.

ARG TMOD_VERSION=v2026.03.2.1

# ── Stage 1: Final image ───────────────────────────────────────────
FROM steamcmd/steamcmd:ubuntu

ARG TMOD_VERSION
ENV SERVER_DIR="/server" \
    DATA_DIR="/data" \
    TMOD_VERSION="${TMOD_VERSION}"

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        wget \
        unzip \
        tmux \
        libsdl2-2.0-0 \
        libicu-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 10000 terraria \
    && useradd -u 10000 -g 10000 -m -d /home/terraria -s /bin/bash terraria

# Create data directories
RUN mkdir -p "${SERVER_DIR}" \
        "${DATA_DIR}/tModLoader/Worlds" \
        "${DATA_DIR}/tModLoader/Mods" \
        "${DATA_DIR}/steamMods"

# Download and install tModLoader
WORKDIR ${SERVER_DIR}
RUN wget -q "https://github.com/tModLoader/tModLoader/releases/download/${TMOD_VERSION}/tModLoader.zip" \
    && unzip -o tModLoader.zip \
    && rm tModLoader.zip \
    && rm -f serverconfig.txt \
    && chmod +x start-tModLoaderServer.sh LaunchUtils/ScriptCaller.sh LaunchUtils/InstallDotNet.sh

# Install .NET runtime (tModLoader's bundled installer puts it in /server/dotnet/)
RUN ./LaunchUtils/InstallDotNet.sh

# Prime steamcmd cache so first run doesn't need to self-update
RUN steamcmd +quit || true

# Hand everything to the non-root user
RUN chown -R 10000:10000 "${SERVER_DIR}" "${DATA_DIR}" /home/terraria

# Copy scripts
COPY --chown=10000:10000 entrypoint.sh prepare-config.sh autosave.sh "${SERVER_DIR}/"
COPY --chown=10000:10000 inject.sh /usr/local/bin/inject
RUN chmod +x "${SERVER_DIR}/entrypoint.sh" "${SERVER_DIR}/prepare-config.sh" \
        "${SERVER_DIR}/autosave.sh" /usr/local/bin/inject

ENV HOME=/home/terraria

EXPOSE 7777/tcp

USER 10000

ENTRYPOINT ["./entrypoint.sh"]
