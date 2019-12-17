FROM nginx:mainline-alpine

RUN openssl dhparam -out /etc/nginx/dhparams.pem 4096
COPY /etc/docker/compose/{{ service_name }}/ssl_cert.pub /etc/nginx/ssl_cert.pub
COPY /etc/docker/compose/{{ service_name }}/ssl_cert.key /etc/nginx/ssl_cert.key
COPY /etc/docker/compose/{{ service_name }}/nginx.app.conf /etc/nginx/conf.d/app.conf
