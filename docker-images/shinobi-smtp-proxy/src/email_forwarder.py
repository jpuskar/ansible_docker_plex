import asyncio
import email
import logging
import smtplib

log = logging.getLogger("smtp-proxy")


class EmailForwarder:
    def __init__(self, host, port):
        self.host = host
        self.port = port

    async def forward(
        self, mail_from, rcpt_tos, message_data, username=None, password=None
    ):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._send, mail_from, rcpt_tos, message_data, username, password
        )

    def _send(self, mail_from, rcpt_tos, message_data, username, password):
        log.info("Forwarding to %s:%s", self.host, self.port)
        with smtplib.SMTP(self.host, self.port) as smtp:
            if username and password:
                smtp.login(username, password)
            smtp.send_message(
                email.message_from_string(message_data),
                from_addr=mail_from,
                to_addrs=rcpt_tos,
            )
        log.info("Forwarded successfully")
