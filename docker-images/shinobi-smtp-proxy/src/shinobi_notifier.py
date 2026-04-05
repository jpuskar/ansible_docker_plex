import json
import logging
import urllib.parse
from urllib.parse import urlparse

import aiohttp

log = logging.getLogger('smtp-proxy')


class ShinobiNotifier:
    """Pushes detection events to Shinobi's motion trigger API.

    Creates timeline markers and can trigger recording in Shinobi.
    Endpoint: GET /{api_key}/motion/{group_key}/{monitor_id}?data={...}
    """

    def __init__(self, base_url, api_key, group_key, monitor_map=None):
        """
        Args:
            base_url: Shinobi base URL (e.g. http://shinobi:8080)
            api_key: Shinobi API key
            group_key: Shinobi group key
            monitor_map: Optional {camera_id: monitor_id} mapping.
                         If not provided, camera_id is used as monitor_id.
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.group_key = group_key
        self.monitor_map = monitor_map or {}
        self._session = None

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def discover_monitors(self, cameras):
        """Auto-discover monitor_map by matching camera IPs to Shinobi monitors.

        Calls GET /{api_key}/monitor/{group_key} to list all monitors,
        extracts each monitor's IP from details.auto_host or the host field,
        and maps our camera_id -> monitor mid.

        Args:
            cameras: dict of {camera_id: ip_address} from our config
        """
        url = f"{self.base_url}/{self.api_key}/monitor/{self.group_key}"
        try:
            session = await self._get_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning("Shinobi monitor list returned %d", resp.status)
                    return
                monitors = await resp.json()
        except Exception:
            log.warning("Failed to fetch Shinobi monitor list", exc_info=True)
            return

        # Build IP -> monitor_id lookup from Shinobi's monitor list
        ip_to_mid = {}
        for mon in monitors:
            mid = mon.get('mid', '')
            name = mon.get('name', '')
            host_ip = None

            details = mon.get('details', {})
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (json.JSONDecodeError, TypeError):
                    details = {}

            # Mode 1: full URL in details.auto_host
            if details.get('auto_host_enable') == '1' and details.get('auto_host'):
                parsed = urlparse(details['auto_host'])
                host_ip = parsed.hostname
            # Mode 2: component fields
            elif mon.get('host'):
                host_ip = mon['host']

            if host_ip:
                ip_to_mid[host_ip] = mid
                log.debug("Shinobi monitor %s (%s) -> IP %s", mid, name, host_ip)

        # Match our camera IPs to Shinobi monitor IDs
        matched = 0
        for camera_id, camera_ip in cameras.items():
            if camera_id in self.monitor_map:
                continue  # explicit mapping takes precedence
            if camera_ip in ip_to_mid:
                self.monitor_map[camera_id] = ip_to_mid[camera_ip]
                matched += 1
                log.info("Mapped %s (%s) -> Shinobi monitor %s",
                         camera_id, camera_ip, ip_to_mid[camera_ip])
            else:
                log.warning("No Shinobi monitor found for %s (%s)", camera_id, camera_ip)

        log.info("Shinobi monitor discovery: %d/%d cameras mapped (%d monitors total)",
                 matched, len(cameras), len(monitors))

    async def trigger_event(self, camera_id, detections, reason=None):
        """Push a detection event to Shinobi's timeline.

        Args:
            camera_id: Our internal camera identifier
            detections: List of Detection objects from YOLO
            reason: Optional reason string (defaults to class names)
        """
        monitor_id = self.monitor_map.get(camera_id, camera_id)
        if not reason:
            reason = ', '.join(sorted(set(d.name for d in detections)))

        matrices = []
        for d in detections:
            matrices.append({
                'tag': d.name,
                'confidence': int(d.conf * 100),
                'x': int((d.cx - d.w / 2) * 704),  # approximate pixel coords
                'y': int((d.cy - d.h / 2) * 480),
                'width': int(d.w * 704),
                'height': int(d.h * 480),
            })

        data = {
            'reason': reason,
            'plug': 'smtp-proxy',
            'name': camera_id,
            'confidence': max(int(d.conf * 100) for d in detections),
            'matrices': matrices,
        }

        url = f"{self.base_url}/{self.api_key}/motion/{self.group_key}/{monitor_id}"
        params = {'data': json.dumps(data)}

        try:
            session = await self._get_session()
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    log.debug("Shinobi event sent for %s/%s", camera_id, monitor_id)
                    return True
                else:
                    body = await resp.text()
                    log.warning("Shinobi API %d for %s: %s", resp.status, camera_id, body[:200])
                    return False
        except Exception:
            log.warning("Shinobi notify failed for %s", camera_id, exc_info=True)
            return False
