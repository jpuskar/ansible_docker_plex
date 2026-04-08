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

import base64, json, logging, os, signal, socket, ssl, struct, sys, time
from types import FrameType
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

prog = "godaddy-ddns"
version = "2.0"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(prog)

BACKOFF_INITIAL = 60
BACKOFF_MAX = 3600

# GoDaddy's authoritative nameservers — bypass cluster/ISP DNS caching
GODADDY_NS = ["ns73.domaincontrol.com", "ns74.domaincontrol.com"]

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
    """Get our current public IP from an external service."""
    with urlopen(
        Request("https://checkip.amazonaws.com/", headers={"User-Agent": "Mozilla"})
    ) as f:
        return f.read().decode("utf-8").strip()


def _build_dns_query(hostname: str) -> bytes:
    """Build a minimal DNS A-record query packet."""
    # Header: ID=0xABCD, flags=0x0100 (standard query, recursion desired),
    # QDCOUNT=1, ANCOUNT=0, NSCOUNT=0, ARCOUNT=0
    header = struct.pack(">HHHHHH", 0xABCD, 0x0100, 1, 0, 0, 0)
    # Question section: encode hostname labels
    question = b""
    for label in hostname.split("."):
        encoded = label.encode("ascii")
        question += struct.pack("B", len(encoded)) + encoded
    question += b"\x00"  # root label
    question += struct.pack(">HH", 1, 1)  # QTYPE=A, QCLASS=IN
    return header + question


def _parse_dns_response(data: bytes) -> str | None:
    """Parse a DNS response and return the first A-record IP, or None."""
    if len(data) < 12:
        return None
    ancount = struct.unpack(">H", data[6:8])[0]
    # Skip header (12 bytes) and question section
    offset = 12
    # Skip question: labels then QTYPE+QCLASS
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length >= 0xC0:  # pointer
            offset += 2
            break
        offset += 1 + length
    offset += 4  # QTYPE + QCLASS
    # Parse answer records
    for _ in range(ancount):
        if offset + 12 > len(data):
            break
        # Name (could be pointer or labels)
        if data[offset] >= 0xC0:
            offset += 2
        else:
            while offset < len(data) and data[offset] != 0:
                if data[offset] >= 0xC0:
                    offset += 2
                    break
                offset += 1 + data[offset]
            else:
                offset += 1
        if offset + 10 > len(data):
            break
        rtype, rclass, _, rdlength = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        if rtype == 1 and rclass == 1 and rdlength == 4:  # A record
            return "{}.{}.{}.{}".format(*data[offset : offset + 4])
        offset += rdlength
    return None


def resolve_via_godaddy(hostname: str) -> str | None:
    """Resolve hostname by querying GoDaddy's authoritative nameservers directly.

    Uses raw UDP DNS queries to bypass all local/cluster DNS caching.
    """
    query = _build_dns_query(hostname)
    for ns in GODADDY_NS:
        try:
            ns_ip = socket.gethostbyname(ns)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)
            try:
                sock.sendto(query, (ns_ip, 53))
                data, _ = sock.recvfrom(512)
                result = _parse_dns_response(data)
                if result:
                    return result
            finally:
                sock.close()
        except (socket.error, OSError) as e:
            log.warning("DNS query to %s failed: %s", ns, e)
            continue
    return None


def update_dns(
    hostname: str,
    api_key: str,
    api_secret: str,
    ttl: int,
    last_ip: str,
) -> tuple[bool, str]:
    """Check and update GoDaddy DNS.

    Returns (success, last_written_ip). last_written_ip is updated only
    when an actual API write occurs.
    """
    log.info("--- Check cycle start for %s ---", hostname)

    hostnames = hostname.split(".")
    if len(hostnames) < 2:
        log.error('Hostname "%s" is not a fully-qualified host name.', hostname)
        return False, last_ip
    elif len(hostnames) < 3:
        hostnames.insert(0, "@")

    # Step 1: What is our public IP?
    try:
        ip = get_public_ip()
        log.info("Public IP: %s", ip)
    except (URLError, OSError) as e:
        log.error("Failed to detect public IP: %s", e)
        return False, last_ip

    octets = ip.split(".")
    if len(octets) != 4 or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        log.error('"%s" is not a valid IPv4 address.', ip)
        return False, last_ip

    # Step 2: Did we already push this IP? If so, skip — DNS may not have propagated yet.
    if ip == last_ip:
        log.info(
            "Public IP %s matches last written IP. Skipping update (waiting for DNS propagation).",
            ip,
        )
        return True, last_ip

    # Step 3: What does GoDaddy's authoritative DNS say?
    record_name = hostnames[0]
    domain = ".".join(hostnames[1:])
    dns_ip = resolve_via_godaddy(hostname)
    if dns_ip:
        log.info("GoDaddy DNS returns %s for %s.", dns_ip, hostname)
        if dns_ip == ip:
            log.info(
                "GoDaddy authoritative DNS already has %s — no update needed. Recording as last written IP.",
                ip,
            )
            return True, ip
        log.info(
            "GoDaddy authoritative DNS has %s but public IP is %s — update required.",
            dns_ip,
            ip,
        )
    else:
        log.warning(
            "Could not resolve %s via GoDaddy nameservers. Will attempt update.",
            hostname,
        )

    # Step 4: Push the update to GoDaddy API
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

    log.info("Sending PUT to GoDaddy API: %s -> %s", hostname, ip)
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
            "UPDATE FAILED: %s",
            messages.get(
                e.code, "GoDaddy API failure: HTTP {} {}".format(e.code, e.reason)
            ),
        )
        return False, last_ip
    except URLError as e:
        log.error("UPDATE FAILED: GoDaddy API connection failure: %s", e.reason)
        return False, last_ip

    log.info(
        "UPDATE SUCCESS: %s set to %s. Will not re-push this IP until it changes.",
        hostname,
        ip,
    )
    return True, ip


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
    last_ip = ""
    while not shutdown:
        ok, last_ip = update_dns(domain, api_key, api_secret, ttl, last_ip)
        if ok:
            backoff = BACKOFF_INITIAL
            sleep_time = interval
            log.info("Next check in %ds.", interval)
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
