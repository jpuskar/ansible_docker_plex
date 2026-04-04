import logging

from email_utils import extract_images

log = logging.getLogger('smtp-proxy')


class EmailFilter:
    """Decides whether to drop an email based on subject keywords and image content."""

    def __init__(self, filter_keywords, object_detector=None):
        self.filter_keywords = [kw.lower() for kw in filter_keywords]
        self.object_detector = object_detector

    async def should_filter(self, message, subject):
        """Returns (should_drop, reason)."""
        # Fast path: keyword match on subject
        subject_lower = subject.lower()
        for kw in self.filter_keywords:
            if kw in subject_lower:
                log.info("Filtered by keyword '%s' (subject: %s)", kw, subject)
                return True, f"keyword: {kw}"

        # AI image check
        images = extract_images(message)
        if not images:
            return False, "no images, passed"

        if self.object_detector is None:
            return False, "no detector, passed"

        for image_data in images:
            try:
                if await self.object_detector.detect(image_data):
                    log.info("Object detected in image, forwarding")
                    return False, "object detected"
            except Exception:
                log.exception("Image detection error, allowing through")
                return False, "detection error, allowing"

        log.info("Filtered: no relevant objects in %d image(s)", len(images))
        return True, "no objects detected in images"
