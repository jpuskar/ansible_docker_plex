import logging

from email_utils import extract_images

log = logging.getLogger('smtp-proxy')


class EmailFilter:
    """Decides whether to drop an email based on subject keywords and image content."""

    def __init__(self, filter_keywords, object_detector=None, baseline_manager=None):
        self.filter_keywords = [kw.lower() for kw in filter_keywords]
        self.object_detector = object_detector
        self.baseline_manager = baseline_manager

    async def should_filter(self, message, subject, camera_id=None):
        """Returns (should_drop, reason)."""
        # Fast path: keyword match on subject
        subject_lower = subject.lower()
        for kw in self.filter_keywords:
            if kw in subject_lower:
                log.info("Filtered by keyword '%s' (subject: %s)", kw, subject)
                return True, f"keyword: {kw}"

        # No images = nothing worth forwarding
        images = extract_images(message)
        if not images:
            log.info("Filtered: no images attached (subject: %s)", subject)
            return True, "no images"

        if self.object_detector is None:
            return False, "no detector, passed"

        # If we have a baseline manager with rolling buffer, use that
        # (analyzes pre-event + post-event frames from camera, not just email attachment)
        if self.baseline_manager and camera_id:
            try:
                has_new, reason = await self.baseline_manager.analyze_event(camera_id)
                if has_new:
                    return False, reason
                else:
                    return True, reason
            except Exception:
                log.exception("Event analysis error, falling back to email image")

        # Fallback: analyze the email's attached image directly
        for image_data in images:
            try:
                detections = await self.object_detector.get_detections(image_data)
                if detections:
                    names = ', '.join(d.name for d in detections)
                    log.info("Objects detected in email image: %s", names)
                    return False, f"objects in email: {names}"
            except Exception:
                log.exception("Image detection error, allowing through")
                return False, "detection error, allowing"

        log.info("Filtered: no relevant objects in %d image(s)", len(images))
        return True, "no objects detected in images"
