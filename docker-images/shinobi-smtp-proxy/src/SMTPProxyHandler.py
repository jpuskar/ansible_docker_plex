import email
import logging

from email_utils import decode_subject, create_forwarded_message
from email_filter import EmailFilter
from email_forwarder import EmailForwarder
from object_detector import ObjectDetector

log = logging.getLogger("smtp-proxy")

# COCO class IDs: people, vehicles, animals
TARGET_CLASSES = [0, 1, 2, 3, 5, 7, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]


class SMTPProxyHandler:
    """Filters camera alert emails (subject keywords + YOLO image detection)
    and forwards survivors to the real Shinobi SMTP endpoint."""

    def __init__(
        self,
        forward_host,
        forward_port,
        fallback_subject,
        filter_keywords,
        ai_detection_enabled=True,
        confidence_threshold=0.25,
        debug_mime=False,
        baseline_manager=None,
        discord_notifier=None,
    ):
        self.fallback_subject = fallback_subject
        self.debug_mime = debug_mime
        self.discord_notifier = discord_notifier

        detector = None
        if ai_detection_enabled:
            detector = ObjectDetector(
                confidence_threshold=confidence_threshold,
                target_classes=TARGET_CLASSES,
            )

        self.email_filter = EmailFilter(filter_keywords, detector, baseline_manager)
        self.email_forwarder = EmailForwarder(forward_host, forward_port)

    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        log.debug("MAIL FROM: %s", address)
        envelope.mail_from = address
        return "250 OK"

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        log.debug("RCPT TO: %s", address)
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        try:
            log.info("Email from %s to %s", envelope.mail_from, envelope.rcpt_tos)

            username = getattr(session, "username", None)
            password = getattr(session, "password", None)

            message = email.message_from_bytes(envelope.content)

            if self.debug_mime:
                if message.is_multipart():
                    parts = [
                        (p.get_content_type(), len(p.get_payload(decode=True) or b""))
                        for p in message.walk()
                        if p.get_content_maintype() != "multipart"
                    ]
                    log.info("MIME parts: %s", parts)
                else:
                    log.info(
                        "Not multipart: %s (size %d)",
                        message.get_content_type(),
                        len(envelope.content),
                    )

            original_subject = message.get("Subject", self.fallback_subject)
            subject = decode_subject(original_subject, self.fallback_subject)

            # Extract camera ID from sender, e.g. camnorthtoeast@spaceskippy.net -> camnorthtoeast
            camera_id = envelope.mail_from.split("@")[0] if envelope.mail_from else None

            should_filter, reason, alert_frame = await self.email_filter.should_filter(
                message, subject, camera_id
            )
            if should_filter:
                log.info("Filtered: %s (subject: %s)", reason, subject)
                return "250 OK"

            log.info("Passed filter: %s (subject: %s)", reason, subject)

            # Send Discord alert with the detection frame if configured
            if self.discord_notifier:
                description = f"{subject}\n{reason}"
                await self.discord_notifier.send_alert(
                    camera_id or "unknown", description, alert_frame
                )

            new_message = create_forwarded_message(
                message, envelope.mail_from, envelope.rcpt_tos, subject
            )

            await self.email_forwarder.forward(
                envelope.mail_from,
                envelope.rcpt_tos,
                new_message.as_string(),
                username,
                password,
            )

            return "250 OK"
        except Exception:
            log.exception("Error processing email")
            return "451 Temporary failure"
