import email.header
import logging
import re

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders

log = logging.getLogger("smtp-proxy")


def decode_subject(raw_subject, fallback_subject):
    """Decode an RFC 2047 subject line to clean ASCII."""
    try:
        parts = email.header.decode_header(raw_subject)
        decoded = ""
        for chunk, charset in parts:
            if isinstance(chunk, bytes):
                decoded += chunk.decode(charset or "utf-8", errors="replace")
            else:
                decoded += chunk

        # Collapse to ASCII, strip garbage
        cleaned = decoded.encode("ascii", errors="replace").decode("ascii")
        cleaned = re.sub(r"\?+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        if not cleaned:
            cleaned = fallback_subject

        log.debug("Subject decoded: '%s' -> '%s'", raw_subject, cleaned)
        return cleaned
    except Exception:
        log.warning("Failed to decode subject '%s'", raw_subject, exc_info=True)
        return fallback_subject


def extract_images(message):
    """Return list of raw image bytes from a message's MIME parts.
    Also checks application/octet-stream parts for image magic bytes."""
    images = []
    for part in message.walk():
        ct = part.get_content_type()
        data = part.get_payload(decode=True)
        if not data:
            continue
        if ct.startswith("image/"):
            images.append(data)
        elif ct == "application/octet-stream" and _looks_like_image(data):
            images.append(data)
    return images


def _looks_like_image(data):
    """Check if raw bytes start with known image magic bytes."""
    if len(data) < 4:
        return False
    # JPEG, PNG, GIF, BMP, WEBP
    return (
        data[:2] == b"\xff\xd8"  # JPEG
        or data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG
        or data[:4] == b"GIF8"  # GIF
        or data[:2] == b"BM"  # BMP
        or data[8:12] == b"WEBP"
    )  # WEBP


def create_forwarded_message(original, mail_from, rcpt_tos, subject):
    """Build a new MIME message with a cleaned subject, preserving attachments."""
    msg = MIMEMultipart()
    msg["From"] = mail_from
    msg["To"] = ", ".join(rcpt_tos)
    msg["Subject"] = subject

    # Copy headers we're not overriding
    for key, value in original.items():
        if key.lower() not in ("from", "to", "subject"):
            msg[key] = value

    if original.is_multipart():
        for part in original.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                subtype = "html" if ct == "text/html" else "plain"
                text = payload.decode(charset, errors="replace")
                msg.attach(MIMEText(text, subtype, "utf-8"))
            elif ct.startswith("image/"):
                data = part.get_payload(decode=True)
                if data:
                    img = MIMEImage(data, _subtype=ct.split("/")[1])
                    for hdr in ("Content-Disposition", "Content-ID"):
                        if part.get(hdr):
                            img[hdr] = part[hdr]
                    msg.attach(img)
            elif part.get_content_maintype() != "multipart":
                data = part.get_payload(decode=True)
                if data:
                    att = MIMEBase(
                        part.get_content_maintype(), part.get_content_subtype()
                    )
                    att.set_payload(data)
                    encoders.encode_base64(att)
                    if part.get("Content-Disposition"):
                        att["Content-Disposition"] = part["Content-Disposition"]
                    msg.attach(att)
    else:
        msg.attach(MIMEText(original.get_payload(), "plain"))

    return msg
