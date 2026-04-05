from __future__ import annotations

import logging
from email.message import Message
from typing import TYPE_CHECKING

from email_utils import extract_images

if TYPE_CHECKING:
    from baseline_manager import BaselineManager
    from object_detector import ObjectDetector

log = logging.getLogger("smtp-proxy")


class EmailFilter:
    """Decides whether to drop an email based on subject keywords and image content."""

    def __init__(self, filter_keywords: list[str], object_detector: ObjectDetector | None = None,
                 baseline_manager: BaselineManager | None = None) -> None:
        self.filter_keywords = [kw.lower() for kw in filter_keywords]
        self.object_detector = object_detector
        self.baseline_manager = baseline_manager

    async def should_filter(self, message: Message, subject: str,
                            camera_id: str | None = None) -> tuple[bool, str, bytes | None]:
        """Returns (should_drop, reason, alert_frame_jpeg_or_None)."""
        # Fast path: keyword match on subject
        subject_lower = subject.lower()
        for kw in self.filter_keywords:
            if kw in subject_lower:
                return True, f"keyword: {kw}", None

        # No images = nothing worth forwarding
        images = extract_images(message)
        if not images:
            return True, "no images", None

        if self.object_detector is None:
            return False, "no detector, passed", None

        # If we have a baseline manager with rolling buffer, use that
        # (analyzes pre-event + post-event frames from camera, not just email attachment)
        if self.baseline_manager and camera_id:
            try:
                has_new, reason, frame = await self.baseline_manager.analyze_event(
                    camera_id
                )
                if has_new:
                    return False, reason, frame
                else:
                    return True, reason, None
            except Exception:
                log.exception("Event analysis error, falling back to email image")

        # Fallback: analyze the email's attached image directly
        for image_data in images:
            try:
                detections = await self.object_detector.get_detections(image_data)
                if detections:
                    names = ", ".join(d.name for d in detections)
                    return False, f"objects in email: {names}", image_data
            except Exception:
                log.exception("Image detection error, allowing through")
                return False, "detection error, allowing", None

        return True, "no objects detected in images", None
