from __future__ import annotations

import email
import logging
from typing import TYPE_CHECKING

from email_utils import decode_subject, create_forwarded_message
from email_filter import EmailFilter
from email_forwarder import EmailForwarder
from object_detector import ObjectDetector

if TYPE_CHECKING:
    from baseline_manager import BaselineManager
    from discord_notifier import DiscordNotifier

log = logging.getLogger("smtp-proxy")

# COCO class IDs: people, vehicles, animals
TARGET_CLASSES = [0, 1, 2, 3, 5, 7, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]


class SMTPProxyHandler:
    """Filters camera alert emails (subject keywords + YOLO image detection)
    and forwards survivors to the real Shinobi SMTP endpoint."""

    def __init__(
        self,
        forward_host: str,
        forward_port: int,
        fallback_subject: str,
        filter_keywords: list[str],
        ai_detection_enabled: bool = True,
        confidence_threshold: float = 0.25,
        debug_mime: bool = False,
        baseline_manager: BaselineManager | None = None,
        discord_notifier: DiscordNotifier | None = None,
    ) -> None:
        self.fallback_subject = fallback_subject
        self.debug_mime = debug_mime
        self.discord_notifier = discord_notifier

        detector = None
        if ai_detection_enabled:
            detector = ObjectDetector(
                confidence_threshold=confidence_threshold,
                target_classes=TARGET_CLASSES,
            )

        self.email_filter = EmailFilter(
            filter_keywords=filter_keywords,
            object_detector=detector,
            baseline_manager=baseline_manager,
        )
        self.email_forwarder = EmailForwarder(host=forward_host, port=forward_port)

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
            subject = decode_subject(raw_subject=original_subject, fallback_subject=self.fallback_subject)

            # Extract camera ID from sender, e.g. camnorthtoeast@spaceskippy.net -> camnorthtoeast
            camera_id = envelope.mail_from.split("@")[0] if envelope.mail_from else None

            should_filter, reason, alert_frame = await self.email_filter.should_filter(
                message=message, subject=subject, camera_id=camera_id,
            )
            if should_filter:
                log.info("Filtered: %s (subject: %s)", reason, subject)
                return "250 OK"

            log.info("Passed filter: %s (subject: %s)", reason, subject)

            # Send Discord alert with the detection frame if configured
            if self.discord_notifier:
                description = f"{subject}\n{reason}"
                await self.discord_notifier.send_alert(
                    camera_id=camera_id or "unknown", description=description,
                    jpeg_bytes=alert_frame,
                )

            new_message = create_forwarded_message(
                original=message, mail_from=envelope.mail_from,
                rcpt_tos=envelope.rcpt_tos, subject=subject,
            )

            await self.email_forwarder.forward(
                mail_from=envelope.mail_from,
                rcpt_tos=envelope.rcpt_tos,
                message_data=new_message.as_string(),
                username=username,
                password=password,
            )

            return "250 OK"
        except Exception:
            log.exception("Error processing email")
            return "451 Temporary failure"
