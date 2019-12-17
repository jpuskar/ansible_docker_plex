FROM nginx:mainline-alpine

RUN openssl dhparam -out /etc/nginx/dhparams.pem 4096
COPY /etc/docker/compose/jenkins/ssl_cert.pub /etc/nginx/ssl_cert.pub
COPY /etc/docker/compose/jenkins/ssl_cert.key /etc/nginx/ssl_cert.key
COPY /etc/docker/compose/jenkins/nginx.app.conf /etc/nginx/conf.d/app.conf
