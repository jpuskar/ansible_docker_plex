from __future__ import annotations

from typing import TYPE_CHECKING

import metrics as m

if TYPE_CHECKING:
    from discord_notifier import DiscordNotifier
    from object_detector import Detection
    from shinobi_notifier import ShinobiNotifier


class AlertDispatcher:
    """Sends alerts to configured destinations from the asyncio event loop."""

    def __init__(
        self,
        discord_notifier: DiscordNotifier | None,
        shinobi_notifier: ShinobiNotifier | None,
    ) -> None:
        self.discord_notifier = discord_notifier
        self.shinobi_notifier = shinobi_notifier

    async def send(
        self,
        camera_id: str,
        description: str,
        jpeg_bytes: bytes,
        detections: list[Detection],
    ) -> None:
        if self.discord_notifier:
            await self.discord_notifier.send_alert(
                camera_id=camera_id,
                description=description,
                jpeg_bytes=jpeg_bytes,
            )
            m.alerts_total.labels(camera=camera_id, destination="discord").inc()
        if self.shinobi_notifier:
            await self.shinobi_notifier.trigger_event(
                camera_id=camera_id,
                detections=detections,
            )
            m.alerts_total.labels(camera=camera_id, destination="shinobi").inc()
