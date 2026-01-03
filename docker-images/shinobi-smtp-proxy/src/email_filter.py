import logging
from email.message import Message
from typing import Optional, Tuple

from object_detector import ObjectDetector
from email_utils import extract_images_from_message


logger = logging.getLogger(__name__)


class EmailFilter:
    """Filters emails based on subject keywords and image content"""

    def __init__(
        self,
        filter_keywords: Tuple[str, ...],
        object_detector: Optional[ObjectDetector] = None
    ) -> None:
        """Initialize the email filter

        Args:
            filter_keywords: Tuple of keywords to filter out from subjects
            object_detector: Optional ObjectDetector instance for image analysis.
                           If None, all emails with images will pass through.
        """
        self.filter_keywords = filter_keywords
        self.object_detector = object_detector

    def should_filter_by_subject(self, subject: str) -> bool:
        """Check if email should be filtered based on subject keywords

        Args:
            subject: The email subject line

        Returns:
            True if email should be filtered (blocked), False otherwise
        """
        if not self.filter_keywords:
            return False

        for keyword in self.filter_keywords:
            if keyword.lower() in subject.lower():
                logger.info(
                    f"FILTERING OUT email with subject '{subject}' (matched keyword: '{keyword}')"
                )
                return True

        return False

    async def should_filter_by_images(self, message: Message) -> Tuple[bool, bool]:
        """Check if email should be filtered based on image content

        Args:
            message: The email message to analyze

        Returns:
            Tuple of (should_filter, has_images)
            - should_filter: True if email should be filtered (blocked), False otherwise
            - has_images: True if email contains image attachments
        """
        # Extract images from message
        images = extract_images_from_message(message)

        if not images:
            # No images, don't filter
            return False, False

        # If no detector configured, allow all emails with images
        if self.object_detector is None:
            logger.info("No object detector configured, allowing email with images")
            return False, True

        # Check each image for target objects
        has_detectable_object = False
        for image_part, image_data in images:
            try:
                detected = await self.object_detector.detect_objects_in_image(image_data)
                if detected:
                    has_detectable_object = True
                    logger.info("Object detected in image, will forward email")
                else:
                    logger.info("No relevant objects detected in image")
            except Exception as e:
                logger.error(f"Error processing image attachment: {e}")
                # On error, assume detection to avoid false negatives
                has_detectable_object = True

        # Filter if we found images but no detectable objects
        if not has_detectable_object:
            logger.info("FILTERING OUT email - no people/vehicles/animals detected in images")
            return True, True

        return False, True

    async def should_filter(self, message: Message, subject: str) -> Tuple[bool, str]:
        """Determine if email should be filtered based on all criteria

        Args:
            message: The email message to analyze
            subject: The cleaned email subject line

        Returns:
            Tuple of (should_filter, reason)
            - should_filter: True if email should be filtered (blocked), False otherwise
            - reason: Human-readable reason for filtering decision
        """
        # Check subject keywords first (faster)
        if self.should_filter_by_subject(subject):
            return True, f"matched keyword filter"

        # Check image content
        should_filter_images, has_images = await self.should_filter_by_images(message)
        if should_filter_images:
            return True, "no objects detected in images"

        # Passed all filters
        return False, "passed all filters"
