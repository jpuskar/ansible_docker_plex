import email
import logging
from typing import Any, List, Optional, Tuple

from email_utils import decode_subject, create_forwarded_message
from email_filter import EmailFilter
from email_forwarder import EmailForwarder
from object_detector import ObjectDetector


logger = logging.getLogger(__name__)


class SMTPProxyHandler:
    """SMTP proxy handler that filters and forwards emails based on content"""

    def __init__(
        self,
        forward_host: str,
        forward_port: int,
        fallback_subject: str,
        filter_keywords: Tuple[str, ...],
        ai_detection_enabled: bool = True,
        confidence_threshold: float = 0.25,
    ) -> None:
        """Initialize the SMTP proxy handler

        Args:
            forward_host: Hostname or IP of the destination SMTP server
            forward_port: Port number of the destination SMTP server
            fallback_subject: Default subject to use if decoding fails
            filter_keywords: Tuple of keywords to filter out from subjects
            ai_detection_enabled: Whether to enable AI object detection
            confidence_threshold: Minimum confidence score for AI detections (0.0-1.0)
        """
        self.fallback_subject = fallback_subject

        # Initialize object detector if enabled
        object_detector: Optional[ObjectDetector] = None
        if ai_detection_enabled:
            # COCO class IDs for people, vehicles, and animals
            # person=0, bicycle=1, car=2, motorcycle=3, bus=5, truck=7,
            # bird=14, cat=15, dog=16, horse=17, sheep=18, cow=19, elephant=20, bear=21, zebra=22, giraffe=23
            target_classes = [0, 1, 2, 3, 5, 7, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]
            object_detector = ObjectDetector(
                model_path='yolov8n.pt',
                confidence_threshold=confidence_threshold,
                target_classes=target_classes
            )

        # Initialize filter and forwarder
        self.email_filter = EmailFilter(filter_keywords, object_detector)
        self.email_forwarder = EmailForwarder(forward_host, forward_port)

    async def handle_MAIL(
        self,
        server: Any,
        session: Any,
        envelope: Any,
        address: str,
        mail_options: List[str]
    ) -> str:
        """Handle SMTP MAIL FROM command

        Args:
            server: SMTP server instance
            session: SMTP session instance
            envelope: Email envelope
            address: Sender email address
            mail_options: SMTP MAIL options

        Returns:
            SMTP response code and message
        """
        logger.info(f"MAIL FROM: {address}")
        envelope.mail_from = address
        return '250 OK'

    async def handle_RCPT(
        self,
        server: Any,
        session: Any,
        envelope: Any,
        address: str,
        rcpt_options: List[str]
    ) -> str:
        """Handle SMTP RCPT TO command

        Args:
            server: SMTP server instance
            session: SMTP session instance
            envelope: Email envelope
            address: Recipient email address
            rcpt_options: SMTP RCPT options

        Returns:
            SMTP response code and message
        """
        logger.info(f"RCPT TO: {address}")
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:
        """Handle SMTP DATA command - processes and forwards email

        Args:
            server: SMTP server instance
            session: SMTP session instance
            envelope: Email envelope with content

        Returns:
            SMTP response code and message
        """
        try:
            logger.info(f"Received email from {envelope.mail_from} to {envelope.rcpt_tos}")

            # Extract credentials from session for forwarding
            username, password = self._extract_credentials(session)

            # Parse the email
            message = email.message_from_bytes(envelope.content)

            # Decode and clean the subject
            original_subject = message.get('Subject', self.fallback_subject)
            clean_subject = decode_subject(original_subject, fallback_subject=self.fallback_subject)

            # Check if email should be filtered
            should_filter, reason = await self.email_filter.should_filter(message, clean_subject)
            if should_filter:
                logger.info(f"Email filtered: {reason}")
                return f'250 Message accepted for delivery (filtered - {reason})'

            logger.info("Email passed filtering checks, forwarding")

            # Create new message with cleaned subject
            new_message = create_forwarded_message(
                message,
                envelope.mail_from,
                envelope.rcpt_tos,
                clean_subject
            )

            # Forward to destination SMTP server
            await self.email_forwarder.forward(
                envelope.mail_from,
                envelope.rcpt_tos,
                new_message.as_string(),
                username,
                password
            )

            return '250 Message accepted for delivery'

        except Exception as e:
            logger.error(f"Error processing email: {e}")
            return '451 Temporary failure'

    def _extract_credentials(self, session: Any) -> Tuple[Optional[str], Optional[str]]:
        """Extract username and password from SMTP session

        Args:
            session: SMTP session instance

        Returns:
            Tuple of (username, password), both may be None
        """
        logger.info("=== SESSION DEBUG ===")
        logger.info(f"Session ID: {id(session)}")

        username: Optional[str] = None
        password: Optional[str] = None

        # Debug log session attributes
        if hasattr(session, 'login_data'):
            logger.info(f"Session login_data type: {type(session.login_data)}")
        if hasattr(session, 'auth_data'):
            logger.info(f"Session auth_data: {'*' * 8 if session.auth_data else 'None'}")
        if hasattr(session, '_login_data'):
            logger.info(f"Session _login_data: {session._login_data}")
        if hasattr(session, 'username'):
            logger.info(f"Session username: {session.username}")
        if hasattr(session, 'password'):
            logger.info(f"Session password: {'*' * 8 if session.password else 'None'}")
        if hasattr(session, 'authenticated'):
            logger.info(f"Session authenticated: {session.authenticated}")

        # Try to extract credentials from auth_data
        if hasattr(session, 'auth_data') and session.auth_data:
            auth_data = session.auth_data
            logger.info(f"Trying to extract from auth_data: {auth_data}")
            if isinstance(auth_data, dict):
                username = auth_data.get('username')
                password = auth_data.get('password')
                logger.info(
                    f"Extracted - Username: {username}, Password: {'*' * 8 if password else 'None'}"
                )

        # Fallback to session attributes
        if not username:
            username = getattr(session, 'username', None)
        if not password:
            password = getattr(session, 'password', None)

        logger.info(
            f"Final credentials - Username: {username}, Password: {'*' * 8 if password else 'None'}"
        )
        logger.info("=== SESSION DEBUG END ===")

        return username, password
