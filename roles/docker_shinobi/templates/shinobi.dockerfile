FROM shinobisystems/shinobi:dev

RUN cd /home/Shinobi \
    && git remote set-url origin https://gitlab.com/Shinobi-Systems/Shinobi.git \
    && git checkout master \
    && git reset --hard \
    && git pull \
    && npm install --unsafe-perm \
    && npm install ffmpeg-static \
    && npm audit fix --force \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y jq

#RUN chown 2022:2022 -R /home/Shinobi \
#    && mkdir -p /var/run/mysqld \
#    && chown 2022:2022 -R /var/run/mysqld \
#    && mkdir -p /var/lib/mysql \
#    && chown mysql -R /var/lib/mysql
#
#USER 2022

COPY docker-entrypoint.sh /
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD [ "pm2-docker", "pm2.yml" ]
