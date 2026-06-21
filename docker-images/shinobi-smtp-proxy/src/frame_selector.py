from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from inference_scheduler import PRIORITY_MOTION, InferenceScheduler
from object_detector import Detection
from proxy_types.pipeline import BestFrameSelection

log = logging.getLogger("smtp-proxy")


class BestFrameSelector:
    """Picks the clearest buffered frame for the detections being alerted."""

    def __init__(
        self,
        get_recent_frames: Callable[[str, float], list[bytes]],
        scheduler: InferenceScheduler,
        position_tolerance: float,
    ) -> None:
        self.get_recent_frames = get_recent_frames
        self.scheduler = scheduler
        self.position_tolerance = position_tolerance

    async def select(
        self,
        camera_id: str,
        jpeg_bytes: bytes,
        all_detections: list[Detection],
        new_detections: list[Detection],
    ) -> BestFrameSelection:
        best_frame = BestFrameSelection(
            jpeg_bytes=jpeg_bytes,
            all_detections=all_detections,
            new_detections=new_detections,
        )
        best_conf = max(d.conf for d in best_frame.new_detections)

        await asyncio.sleep(1.5)

        delayed_frames = self.get_recent_frames(camera_id, 2)
        if not delayed_frames:
            return best_frame

        delayed_jpeg = delayed_frames[-1]
        delayed_dets = await self.scheduler.infer(
            delayed_jpeg,
            priority=PRIORITY_MOTION,
            camera_id=camera_id,
        )
        delayed_new = [
            d for d in delayed_dets
            if any(
                d.is_near(n, tolerance=self.position_tolerance)
                for n in new_detections
            )
        ]
        if not delayed_new:
            return best_frame

        delayed_conf = max(d.conf for d in delayed_new)
        if delayed_conf <= best_conf:
            return best_frame

        log.info(
            "BestFrame %s: using delayed frame (conf %.0f%% > %.0f%%)",
            camera_id,
            delayed_conf * 100,
            best_conf * 100,
        )
        return BestFrameSelection(
            jpeg_bytes=delayed_jpeg,
            all_detections=delayed_dets,
            new_detections=delayed_new,
        )
