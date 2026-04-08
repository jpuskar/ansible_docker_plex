#!/usr/bin/env python3
"""
GoDaddy Dynamic DNS updater — runs as a long-lived process.

Originally from https://github.com/CarlEdman/godaddy-ddns (Unlicense/public domain).
Modified to read credentials from a Kubernetes secret via the in-cluster API
and run in a loop with exponential backoff on failure.

Environment variables:
  GODADDY_SECRET_NAME - K8s secret name to read (default: godaddy-config)
  GODADDY_TTL         - DNS TTL in seconds (default: 3600)
  GODADDY_INTERVAL    - Check interval in seconds (default: 300)

The secret must contain keys: GODADDY_DOMAIN, GODADDY_API_KEY, GODADDY_API_SECRET
"""

import base64, json, logging, os, signal, socket, ssl, sys, time
from types import FrameType
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

prog = "godaddy-ddns"
version = "1.0"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(prog)

BACKOFF_INITIAL = 60
BACKOFF_MAX = 3600

SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SA_CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
SA_NS_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"

shutdown = False


def handle_signal(signum: int, frame: FrameType | None) -> None:
    global shutdown
    log.info("Received signal %d, shutting down.", signum)
    shutdown = True


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def read_k8s_secret(secret_name: str) -> dict[str, str]:
    """Read a secret from the Kubernetes API using in-cluster service account."""
    with open(SA_TOKEN_PATH) as f:
        token = f.read().strip()
    with open(SA_NS_PATH) as f:
        namespace = f.read().strip()

    ctx = ssl.create_default_context(cafile=SA_CA_PATH)
    url = "https://kubernetes.default.svc/api/v1/namespaces/{}/secrets/{}".format(
        namespace, secret_name
    )
    req = Request(url)
    req.add_header("Authorization", "Bearer {}".format(token))
    req.add_header("Accept", "application/json")

    with urlopen(req, context=ctx) as f:
        body = json.loads(f.read().decode("utf-8"))

    data = body.get("data", {})
    return {k: base64.b64decode(v).decode("utf-8") for k, v in data.items()}


def get_public_ip() -> str:
    with urlopen(
        Request("https://checkip.amazonaws.com/", headers={"User-Agent": "Mozilla"})
    ) as f:
        return f.read().decode("utf-8").strip()


def update_dns(hostname: str, api_key: str, api_secret: str, ttl: int) -> bool:
    """Check and update GoDaddy DNS. Returns True on success/no-op, False on failure."""
    log.info("Checking DNS for %s", hostname)

    hostnames = hostname.split(".")
    if len(hostnames) < 2:
        log.error('Hostname "%s" is not a fully-qualified host name.', hostname)
        return False
    elif len(hostnames) < 3:
        hostnames.insert(0, "@")

    try:
        ip = get_public_ip()
        log.info("Detected public IP: %s", ip)
    except (URLError, OSError) as e:
        log.error("Unable to obtain public IP: %s", e)
        return False

    octets = ip.split(".")
    if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        log.error('"%s" is not a valid IPv4 address.', ip)
        return False

    try:
        dnsaddr = socket.gethostbyname(hostname)
        if ip == dnsaddr:
            log.info("%s already has IP %s — no update needed.", hostname, dnsaddr)
            return True
        log.info("DNS has %s, public IP is %s — updating.", dnsaddr, ip)
    except socket.gaierror:
        log.warning("DNS lookup for %s failed, proceeding with update.", hostname)

    record_name = hostnames[0]
    domain = ".".join(hostnames[1:])
    url = "https://api.godaddy.com/v1/domains/{}/records/A/{}".format(
        domain, record_name
    )
    data = json.dumps(
        [{"data": ip, "ttl": ttl, "name": record_name, "type": "A"}]
    ).encode("utf-8")
    req = Request(url, method="PUT", data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", "sso-key {}:{}".format(api_key, api_secret))

    try:
        with urlopen(req) as f:
            f.read()
    except HTTPError as e:
        messages = {
            400: "GoDaddy API URL ({}) was malformed.".format(url),
            401: "GoDaddy API: key or secret incorrect.",
            403: "GoDaddy API: access denied (403). Requires 10+ domains or Premium Discount Domain Club.",
            404: "GoDaddy API: {} not found.".format(hostname),
            422: 'GoDaddy API: "{}" has invalid domain or lacks A record.'.format(
                hostname
            ),
            429: "GoDaddy API: rate limited (429). Too many requests.",
            503: "GoDaddy API: service unavailable (503).",
        }
        log.error(
            messages.get(
                e.code, "GoDaddy API failure: HTTP {} {}".format(e.code, e.reason)
            )
        )
        return False
    except URLError as e:
        log.error("GoDaddy API connection failure: %s", e.reason)
        return False

    log.info("IP address for %s set to %s.", hostname, ip)
    return True


def main() -> int:
    secret_name = os.environ.get("GODADDY_SECRET_NAME", "godaddy-config")
    ttl = int(os.environ.get("GODADDY_TTL", "3600"))
    interval = int(os.environ.get("GODADDY_INTERVAL", "300"))

    log.info(
        "Starting %s v%s — secret=%s interval=%ds ttl=%ds",
        prog,
        version,
        secret_name,
        interval,
        ttl,
    )

    # Read credentials from K8s secret
    try:
        secret_data = read_k8s_secret(secret_name)
    except Exception as e:
        log.error('Failed to read K8s secret "%s": %s', secret_name, e)
        return 1

    domain = secret_data.get("GODADDY_DOMAIN", "").strip()
    api_key = secret_data.get("GODADDY_API_KEY", "").strip()
    api_secret = secret_data.get("GODADDY_API_SECRET", "").strip()

    if not domain or not api_key or not api_secret:
        log.error(
            'Secret "%s" must contain GODADDY_DOMAIN, GODADDY_API_KEY, and GODADDY_API_SECRET.',
            secret_name,
        )
        return 1

    log.info("Loaded credentials for domain=%s", domain)

    backoff = BACKOFF_INITIAL
    while not shutdown:
        ok = update_dns(domain, api_key, api_secret, ttl)
        if ok:
            backoff = BACKOFF_INITIAL
            sleep_time = interval
        else:
            sleep_time = backoff
            log.warning("Backing off — next attempt in %ds.", backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

        for _ in range(sleep_time):
            if shutdown:
                break
            time.sleep(1)

    log.info("Shut down cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
