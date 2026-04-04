
Copy CA cert from secret.
Install into windows trusted certs
restart docker-desktop


docker login harbor.spaceskippy.net
docker build . -t harbor.spaceskippy.net/shinobi-smtp-proxy.latest
docker push harbor.spaceskippy.net/shinobi-smtp-proxy.latest
