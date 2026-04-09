from __future__ import annotations

import asyncio
import io
import json
import logging
import time

import aiohttp

log = logging.getLogger("smtp-proxy")

# Discord bot API base
DISCORD_API = "https://discord.com/api/v10"


class DiscordNotifier:
    """Sends detection alerts to Discord via bot token + channel ID.

    Mimics Shinobi's embed format: author (camera name), title (detection summary),
    description with timestamp, footer, and the detection frame as an attached image.

    Includes per-camera cooldown to avoid flooding the channel.
    """

    def __init__(
        self, bot_token: str, channel_id: str, cooldown_seconds: int = 60,
        bot_name: str = "Shinobi SMTP Proxy",
    ) -> None:
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.cooldown_seconds = cooldown_seconds
        self.bot_name = bot_name
        self._headers = {
            "Authorization": f"Bot {bot_token}",
        }
        self._last_sent = {}  # {camera_id: monotonic timestamp}
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _in_cooldown(self, camera_id: str) -> bool:
        last = self._last_sent.get(camera_id, 0)
        return (time.monotonic() - last) < self.cooldown_seconds

    async def send_alert(self, camera_id: str, description: str,
                         jpeg_bytes: bytes | None = None) -> bool:
        """Send a detection alert to Discord for a camera.

        Args:
            camera_id: Camera identifier (used as embed author name)
            description: Detection description (e.g. "new objects: person, car")
            jpeg_bytes: Optional JPEG image bytes to attach
        """
        if self._in_cooldown(camera_id):
            log.debug("Discord alert suppressed for %s (cooldown)", camera_id)
            return False

        embed = {
            "author": {
                "name": camera_id,
            },
            "title": "Motion Alert",
            "description": description,
            "color": 3447003,  # blue, same as Shinobi
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "footer": {
                "text": self.bot_name,
            },
        }

        if jpeg_bytes:
            embed["image"] = {"url": "attachment://alert.jpg"}

        try:
            session = await self._get_session()
            url = f"{DISCORD_API}/channels/{self.channel_id}/messages"

            if jpeg_bytes:
                form = aiohttp.FormData()
                form.add_field(
                    "payload_json",
                    json.dumps(
                        {
                            "embeds": [embed],
                        }
                    ),
                    content_type="application/json",
                )
                form.add_field(
                    "files[0]",
                    io.BytesIO(jpeg_bytes),
                    filename="alert.jpg",
                    content_type="image/jpeg",
                )
                async with session.post(url, headers=self._headers, data=form) as resp:
                    if resp.status in (200, 201):
                        self._last_sent[camera_id] = time.monotonic()
                        log.info("Discord alert sent for %s", camera_id)
                        return True
                    else:
                        body = await resp.text()
                        log.warning(
                            "Discord API error %d for %s: %s",
                            resp.status,
                            camera_id,
                            body,
                        )
                        return False
            else:
                payload = {"embeds": [embed]}
                async with session.post(
                    url, headers=self._headers, json=payload
                ) as resp:
                    if resp.status in (200, 201):
                        self._last_sent[camera_id] = time.monotonic()
                        log.info("Discord alert sent for %s", camera_id)
                        return True
                    else:
                        body = await resp.text()
                        log.warning(
                            "Discord API error %d for %s: %s",
                            resp.status,
                            camera_id,
                            body,
                        )
                        return False

        except Exception:
            log.warning("Discord send failed for %s", camera_id, exc_info=True)
            return False
