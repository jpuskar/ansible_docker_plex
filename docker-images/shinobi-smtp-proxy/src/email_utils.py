import email
import email.header
import logging
import re

from typing import List, Tuple

from email.message import Message
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase


logger = logging.getLogger(__name__)


def decode_subject(subject_line: str, fallback_subject: str) -> str:
    """Decode email subject line from various encodings to clean ASCII

    Args:
        subject_line: The encoded subject line to decode
        fallback_subject: Subject to use if decoding fails

    Returns:
        Cleaned ASCII-safe subject string
    """
    try:
        # Handle RFC 2047 encoded subjects like =?UTF-8?B?...?=
        decoded_parts = email.header.decode_header(subject_line)
        decoded_subject = ""

        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                if encoding:
                    decoded_text = part.decode(encoding)
                else:
                    # Try common encodings
                    for enc in ['utf-8', 'latin-1', 'ascii']:
                        try:
                            decoded_text = part.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        decoded_text = part.decode('utf-8', errors='replace')
            else:
                decoded_text = part

            decoded_subject += decoded_text

        # Clean up the subject - convert to ASCII-safe characters
        cleaned = decoded_subject.encode('ascii', errors='replace').decode('ascii')
        # Replace question marks (from failed conversions) with spaces
        cleaned = re.sub(r'\?+', ' ', cleaned).strip()

        # If nothing readable remains, use fallback
        if not cleaned or cleaned.isspace():
            cleaned = fallback_subject

        # Final trim of whitespace and ensure no leading/trailing spaces
        cleaned = cleaned.strip()
        # Extra cleaning: remove multiple spaces and trim again
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        logger.info(f"Subject decoded: '{subject_line}' -> '{cleaned}'")
        return cleaned

    except Exception as e:
        logger.warning(f"Failed to decode subject '{subject_line}': {e}")
        return fallback_subject


def extract_images_from_message(message: Message) -> List[Tuple[Message, bytes]]:
    """Extract all image parts from an email message

    Args:
        message: The email message to extract images from

    Returns:
        List of tuples containing (image_part, image_data)
    """
    images = []
    for part in message.walk():
        content_type = part.get_content_type()
        if content_type.startswith('image/'):
            try:
                image_data = part.get_payload(decode=True)
                if image_data:
                    logger.info(f"Found image attachment: {content_type}")
                    images.append((part, image_data))
            except Exception as e:
                logger.error(f"Error extracting image attachment: {e}")
    return images


def create_forwarded_message(
    original_message: Message,
    mail_from: str,
    rcpt_tos: List[str],
    clean_subject: str
) -> MIMEMultipart:
    """Create a new MIME message for forwarding with cleaned subject

    Args:
        original_message: The original email message
        mail_from: Sender email address
        rcpt_tos: List of recipient email addresses
        clean_subject: Cleaned subject line

    Returns:
        New MIMEMultipart message ready for forwarding
    """
    new_message = MIMEMultipart()
    new_message['From'] = mail_from
    new_message['Subject'] = clean_subject
    new_message['To'] = ', '.join(rcpt_tos)

    # Copy other headers (except the ones we're setting)
    for key, value in original_message.items():
        if key.lower() not in ['from', 'subject', 'to']:
            new_message[key] = value

    # Copy all parts including images
    if original_message.is_multipart():
        for part in original_message.walk():
            content_type = part.get_content_type()
            if content_type == 'text/plain':
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'
                new_message.attach(MIMEText(payload.decode(charset, errors='replace'), 'plain', charset))
            elif content_type == 'text/html':
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or 'utf-8'
                new_message.attach(MIMEText(payload.decode(charset, errors='replace'), 'html', charset))
            elif content_type.startswith('image/'):
                # Attach image
                img_data = part.get_payload(decode=True)
                if img_data:
                    img_part = MIMEImage(img_data, _subtype=content_type.split('/')[1])
                    if part.get('Content-Disposition'):
                        img_part['Content-Disposition'] = part.get('Content-Disposition')
                    if part.get('Content-ID'):
                        img_part['Content-ID'] = part.get('Content-ID')
                    new_message.attach(img_part)
            elif part.get_content_maintype() != 'multipart':
                # Attach other types of content
                payload = part.get_payload(decode=True)
                if payload:
                    attachment = MIMEBase(part.get_content_maintype(), part.get_content_subtype())
                    attachment.set_payload(payload)
                    if part.get('Content-Disposition'):
                        attachment['Content-Disposition'] = part.get('Content-Disposition')
                    new_message.attach(attachment)
    else:
        content = original_message.get_payload()
        new_message.attach(MIMEText(content, 'plain'))

    return new_message
