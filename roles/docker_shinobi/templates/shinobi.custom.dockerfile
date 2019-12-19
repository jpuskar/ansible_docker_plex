FROM ubuntu:18.04
ENV DEBIAN_FRONTEND noninteractive

RUN apt-get update \
    && apt-get install apt-utils -y \
    && apt-get upgrade -y

RUN apt-get install git -y
RUN apt-get install mariadb-server -y

RUN mkdir -p /opt/shinobi \
    && pushd /opt/shinobi \
    && git clone https://gitlab.com/Shinobi-Systems/Shinobi.git ./ \
    && git checkout dev \
    && git pull

COPY docker-entrypoint.sh /opt/shinobi/
RUN chown -R 2022:2022 /customAutoLoad
RUN rm -rf /opt/shinobi/.git
RUN chown -R 2022:2022 /opt/shinobi

USER 2022
