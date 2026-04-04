import asyncio
import email
import logging
import smtplib
from typing import List, Optional


logger = logging.getLogger(__name__)


class EmailForwarder:
    """Handles forwarding emails to a destination SMTP server"""

    def __init__(self, forward_host: str, forward_port: int) -> None:
        """Initialize the email forwarder

        Args:
            forward_host: Hostname or IP of the destination SMTP server
            forward_port: Port number of the destination SMTP server
        """
        self.forward_host = forward_host
        self.forward_port = forward_port

    def _forward_sync(
        self,
        mail_from: str,
        rcpt_tos: List[str],
        message_data: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        """Synchronous SMTP forwarding (called via run_in_executor)"""
        logger.info(f"Connecting to {self.forward_host}:{self.forward_port}")
        with smtplib.SMTP(self.forward_host, self.forward_port) as smtp:
            if username and password:
                logger.info(f"Authenticating with username: ({username})")
                smtp.login(username, password)
            else:
                logger.info("No authentication provided, proceeding without auth")

            smtp.send_message(
                email.message_from_string(message_data),
                from_addr=mail_from,
                to_addrs=rcpt_tos
            )

        logger.info(f"Successfully forwarded email to {self.forward_host}:{self.forward_port}")

    async def forward(
        self,
        mail_from: str,
        rcpt_tos: List[str],
        message_data: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        """Forward an email to the destination SMTP server

        Args:
            mail_from: Sender email address
            rcpt_tos: List of recipient email addresses
            message_data: The complete email message as a string
            username: Optional SMTP authentication username
            password: Optional SMTP authentication password

        Raises:
            Exception: If forwarding fails
        """
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._forward_sync,
                mail_from,
                rcpt_tos,
                message_data,
                username,
                password,
            )
        except Exception as e:
            logger.error(f"Failed to forward email: {e}")
            raise
