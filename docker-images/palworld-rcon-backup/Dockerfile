FROM ubuntu:jammy

RUN export DEBIAN_FRONTEND=noninteractive \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
      bash \
      tar \
      tini \
      python3 \
      python3-pip \
      wget

RUN python3 -m pip install \
    --no-cache-dir \
    --upgrade \
        kubernetes \
        PyYAML

RUN wget -q https://github.com/itzg/rcon-cli/releases/download/1.7.4/rcon-cli_1.7.4_linux_amd64.tar.gz -O - | tar -xz
RUN mv rcon-cli /usr/bin/rcon-cli

COPY run-backup.py /run-backup.py
COPY start.sh /start.sh
RUN chmod +x /start.sh

WORKDIR /
USER 2020

ENTRYPOINT ["tini", "-g", "--", "/start.sh"]
