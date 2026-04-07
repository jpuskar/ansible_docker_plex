FROM steamcmd/steamcmd:ubuntu
ENV APP_ID="2394010" \
    APP_PATH="/server"

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
      bash \
      python3 \
      python3-pip \
      tar \
      tini \
      vim  \
      wget \
      xdg-user-dirs

RUN mkdir -p "${APP_PATH}/steamapps" \
    && steamcmd \
        +force_install_dir "${APP_PATH}" \
        +login anonymous \
        +app_update ${APP_ID} \
        validate  \
        +quit \
    && chown -R 2020:2020 /server

RUN python3 -m pip install \
    --no-cache-dir \
    --upgrade \
    --break-system-packages \
        kubernetes \
        PyYAML

RUN mkdir -p /home/steam/.steam/
RUN ln -s /server/linux64 /home/steam/.steam/sdk64
RUN chown 2020:2020 -R /home/steam

RUN mkdir -p /steam && chown 2020:2020 /steam
RUN mkdir -p /server/Pal/Saved/Config/LinuxServer/ \
    && chown -R 2020:2020 /server/Pal/Saved/Config/LinuxServer/

RUN mkdir -p "${APP_PATH}/steamapps" \
    && steamcmd \
        +login anonymous \
        +app_update 1007 \
        validate  \
        +quit

RUN wget -q https://github.com/itzg/rcon-cli/releases/download/1.6.9/rcon-cli_1.6.9_linux_amd64.tar.gz -O - | tar -xz
RUN mv rcon-cli /usr/bin/rcon-cli

RUN cd /server/Pal/Saved && tar -czvf /default_config.tar.gz ./Config

COPY configure.py /configure.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

WORKDIR /
USER 2020

ENTRYPOINT ["tini", "-g", "--", "/start.sh"]
