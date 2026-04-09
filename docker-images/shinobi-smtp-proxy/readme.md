
Copy CA cert from secret.
Install into windows trusted certs
restart docker-desktop


docker login the.harbor.fqdn
docker build . -t the.harbor.fqdn/shinobi-smtp-proxy.latest
docker push the.harbor.fqdn/shinobi-smtp-proxy.latest
