# REF: https://hub.docker.com/r/shinobicctv/shinobi/dockerfile
FROM shinobisystems/shinobi:latest-ubuntu

# update
RUN cd /opt/shinobi \
    && git checkout dev \
    && git reset --hard \
    && git pull \
    && npm install --unsafe-perm \
    && npm audit fix --force

COPY docker-entrypoint.sh /opt/shinobi/

# RUN chown -R 2022:2022 /customAutoLoad
# RUN rm -rf /opt/shinobi/.git
# RUN chown -R 2022:2022 /opt/shinobi
#
# USER 2022
