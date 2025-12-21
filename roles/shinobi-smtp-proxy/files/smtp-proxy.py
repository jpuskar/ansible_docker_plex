import asyncio
import email
import email.header
import logging
import re
import yaml
import base64
import io
import os
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPServer, AuthResult
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from PIL import Image
from ultralytics import YOLO
import numpy as np

# Load configuration
with open('/config/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Setup logging
logging.basicConfig(
    level=getattr(logging, config['log_level']),
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def decode_subject(subject_line):
    """Decode email subject line from various encodings to clean ASCII"""
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
            cleaned = config['fallback_subject']

        # Final trim of whitespace and ensure no leading/trailing spaces
        cleaned = cleaned.strip()
        # Extra cleaning: remove multiple spaces and trim again
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        logger.info(f"Subject decoded: '{subject_line}' -> '{cleaned}'")
        return cleaned

    except Exception as e:
        logger.warning(f"Failed to decode subject '{subject_line}': {e}")
        return config['fallback_subject']


class SMTPProxyHandler:
    def __init__(self):
        self.forward_host = config['forward_host']
        self.forward_port = config['forward_port']
        self.ai_detection_enabled = config.get('ai_detection_enabled', True)
        self.confidence_threshold = config.get('confidence_threshold', 0.25)
        self.model = None

        # COCO class IDs for people, vehicles, and animals
        # person=0, bicycle=1, car=2, motorcycle=3, bus=5, truck=7,
        # bird=14, cat=15, dog=16, horse=17, sheep=18, cow=19, elephant=20, bear=21, zebra=22, giraffe=23
        self.target_classes = [0, 1, 2, 3, 5, 7, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23]

        # Initialize YOLO model if detection is enabled
        if self.ai_detection_enabled:
            try:
                logger.info("Loading YOLOv8n model...")
                self.model = YOLO('yolov8n.pt')  # Nano model - fastest
                logger.info("YOLOv8n model loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load YOLO model: {e}")
                self.ai_detection_enabled = False

    async def detect_objects_in_image(self, image_data):
        """Use local YOLOv8 to detect people, vehicles, or animals in an image"""
        try:
            if not self.ai_detection_enabled or self.model is None:
                logger.info("AI detection disabled, allowing email through")
                return True

            # Load image from bytes
            img = Image.open(io.BytesIO(image_data))

            # Run YOLO detection (runs in thread pool to avoid blocking)
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.model.predict(img, conf=self.confidence_threshold, imgsz=416, verbose=False)
            )

            # Check if any target objects were detected
            for result in results:
                if result.boxes is not None and len(result.boxes) > 0:
                    detected_classes = result.boxes.cls.cpu().numpy().astype(int)
                    for cls_id in detected_classes:
                        if cls_id in self.target_classes:
                            class_name = result.names[cls_id]
                            logger.info(f"Detected {class_name} in image (confidence: {result.boxes.conf[detected_classes == cls_id].max():.2f})")
                            return True

            logger.info("No people/vehicles/animals detected in image")
            return False

        except Exception as e:
            logger.error(f"Error during AI detection: {e}")
            return True  # Allow through on error to avoid false negatives

    async def handle_MAIL(self, server, session, envelope, address, mail_options):
        logger.info(f"MAIL FROM: {address}")
        envelope.mail_from = address
        return '250 OK'

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        logger.info(f"RCPT TO: {address}")
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(self, server, session, envelope):
        try:
            logger.info(f"Received email from {envelope.mail_from} to {envelope.rcpt_tos}")

            # Debug session attributes
            logger.info(f"=== SESSION DEBUG ===")
            logger.info(f"Session ID: {id(session)}")
            if hasattr(session, 'login_data'):
                logger.info(f"Session login_data type: {type(session.login_data)}")
            if hasattr(session, 'auth_data'):
                logger.info(f"Session auth_data: {session.auth_data}")
            if hasattr(session, '_login_data'):
                logger.info(f"Session _login_data: {session._login_data}")
            if hasattr(session, 'username'):
                logger.info(f"Session username: {session.username}")
            if hasattr(session, 'password'):
                logger.info(f"Session password: {'*' * len(session.password) if session.password else 'None'}")
            if hasattr(session, 'authenticated'):
                logger.info(f"Session authenticated: {session.authenticated}")

            # Try to extract credentials from auth_data
            username = None
            password = None
            if hasattr(session, 'auth_data') and session.auth_data:
                auth_data = session.auth_data
                logger.info(f"Trying to extract from auth_data: {auth_data}")
                if isinstance(auth_data, dict):
                    username = auth_data.get('username')
                    password = auth_data.get('password')
                    logger.info(
                        f"Extracted - Username: {username}, Password: {'*' * len(password) if password else 'None'}")

            logger.info(f"=== SESSION DEBUG END ===")

            # Parse the email
            message = email.message_from_bytes(envelope.content)

            # Decode and clean the subject
            original_subject = message.get('Subject', config['fallback_subject'])
            clean_subject = decode_subject(original_subject)

            # Check if subject should be filtered out
            filter_keywords = config.get('filter_keywords', [])
            if filter_keywords:
                for keyword in filter_keywords:
                    if keyword.lower() in clean_subject.lower():
                        logger.info(
                            f"FILTERING OUT email with subject '{clean_subject}' (matched keyword: '{keyword}')")
                        return '250 Message accepted for delivery (filtered)'

            # Extract and analyze images with AI detection
            has_detectable_object = False
            image_attachments = []

            for part in message.walk():
                content_type = part.get_content_type()
                if content_type.startswith('image/'):
                    try:
                        image_data = part.get_payload(decode=True)
                        if image_data:
                            logger.info(f"Found image attachment: {content_type}")
                            image_attachments.append(part)

                            # Check if image contains people, vehicles, or animals
                            detected = await self.detect_objects_in_image(image_data)
                            if detected:
                                has_detectable_object = True
                                logger.info("Object detected in image, will forward email")
                            else:
                                logger.info("No relevant objects detected in image")
                    except Exception as e:
                        logger.error(f"Error processing image attachment: {e}")
                        # On error, assume detection to avoid false negatives
                        has_detectable_object = True

            # If we found images but no detectable objects, filter out the email
            if image_attachments and not has_detectable_object:
                logger.info(f"FILTERING OUT email - no people/vehicles/animals detected in images")
                return '250 Message accepted for delivery (filtered - no objects detected)'

            logger.info(f"Email passed filtering checks, forwarding to Shinobi")

            # Create new message with cleaned subject
            new_message = MIMEMultipart()
            new_message['From'] = envelope.mail_from
            new_message['Subject'] = clean_subject
            new_message['To'] = ', '.join(envelope.rcpt_tos)

            # Copy other headers (except the ones we're setting)
            for key, value in message.items():
                if key.lower() not in ['from', 'subject', 'to']:
                    new_message[key] = value

            # Copy all parts including images
            if message.is_multipart():
                for part in message.walk():
                    content_type = part.get_content_type()
                    if content_type == 'text/plain':
                        new_message.attach(MIMEText(part.get_payload(decode=False), 'plain'))
                    elif content_type == 'text/html':
                        new_message.attach(MIMEText(part.get_payload(decode=False), 'html'))
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
                content = message.get_payload()
                new_message.attach(MIMEText(content, 'plain'))

            # Forward to Shinobi with authentication from session or extracted auth_data
            final_username = username or getattr(session, 'username', None)
            final_password = password or getattr(session, 'password', None)

            logger.info(
                f"Using credentials - Username: {final_username}, Password: {'*' * len(final_password) if final_password else 'None'}")

            await self.forward_email(
                envelope,
                new_message.as_string(),
                final_username,
                final_password
            )

            return '250 Message accepted for delivery'

        except Exception as e:
            logger.error(f"Error processing email: {e}")
            return '451 Temporary failure'

    async def forward_email(self, envelope, message_data, username=None, password=None):
        """Forward the cleaned email to Shinobi SMTP server"""
        try:
            logger.info(f"Connecting to {self.forward_host}:{self.forward_port}")
            with smtplib.SMTP(self.forward_host, self.forward_port) as smtp:
                # Use authentication if provided
                if username and password:
                    logger.info(f"Authenticating with username: {username}")
                    smtp.login(username, password)
                else:
                    logger.info("No authentication provided, proceeding without auth")

                smtp.send_message(
                    email.message_from_string(message_data),
                    from_addr=envelope.mail_from,
                    to_addrs=envelope.rcpt_tos
                )

            logger.info(f"Successfully forwarded email to {self.forward_host}:{self.forward_port}")

        except Exception as e:
            logger.error(f"Failed to forward email: {e}")
            raise


def authenticator_callback(server, session, envelope, mechanism, auth_data):
    """Proper aiosmtpd authenticator callback"""
    try:
        logger.info(f"=== AUTH CALLBACK START ===")
        logger.info(f"AUTH mechanism: {mechanism}")
        logger.info(f"AUTH data: {auth_data}")
        logger.info(f"Session ID: {id(session)}")

        if mechanism == 'LOGIN':
            # For LOGIN, auth_data is a LoginPassword object with .login and .password
            try:
                if hasattr(auth_data, 'login') and hasattr(auth_data, 'password'):
                    username = auth_data.login
                    password = auth_data.password

                    # Convert bytes to string if needed
                    if isinstance(username, bytes):
                        username = username.decode('utf-8')
                    if isinstance(password, bytes):
                        password = password.decode('utf-8')

                    # Store credentials in session for later use
                    session.username = username
                    session.password = password

                    logger.info(f"LOGIN auth - Username: {username}, Password: {'*' * len(password)}")
                    return AuthResult(success=True)
                elif isinstance(auth_data, dict) and 'username' in auth_data and 'password' in auth_data:
                    # Fallback for dict format
                    username = auth_data['username']
                    password = auth_data['password']

                    session.username = username
                    session.password = password

                    logger.info(f"LOGIN auth (dict) - Username: {username}, Password: {'*' * len(password)}")
                    return AuthResult(success=True)
                else:
                    logger.error(f"LOGIN auth failed - invalid auth_data format: {auth_data}")
                    logger.error(f"auth_data type: {type(auth_data)}, dir: {dir(auth_data)}")
                    return AuthResult(success=False, message="Invalid credentials format")
            except Exception as login_err:
                logger.error(f"LOGIN auth processing error: {login_err}")
                return AuthResult(success=False, message="Login processing error")

        elif mechanism == 'PLAIN':
            # For PLAIN, auth_data should be decoded
            try:
                if isinstance(auth_data, str):
                    # Decode base64 if needed
                    try:
                        decoded = base64.b64decode(auth_data).decode('utf-8')
                    except:
                        decoded = auth_data
                else:
                    decoded = str(auth_data)

                parts = decoded.split('\x00')
                logger.info(f"PLAIN auth parts: {len(parts)}")

                if len(parts) >= 3:
                    username = parts[1]
                    password = parts[2]

                    # Store credentials in session
                    session.username = username
                    session.password = password

                    logger.info(f"PLAIN auth - Username: {username}, Password: {'*' * len(password)}")
                    return AuthResult(success=True)
                else:
                    logger.error(f"PLAIN auth failed - insufficient parts: {parts}")
                    return AuthResult(success=False, message="Invalid PLAIN format")

            except Exception as plain_err:
                logger.error(f"PLAIN auth decode error: {plain_err}")
                return AuthResult(success=False, message="Decode error")

        logger.error(f"Unsupported AUTH mechanism: {mechanism}")
        return AuthResult(success=False, message="Unsupported mechanism")

    except Exception as e:
        logger.error(f"Authenticator exception: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return AuthResult(success=False, message="Authentication error")
    finally:
        logger.info(f"=== AUTH CALLBACK END ===")


async def main():
    handler = SMTPProxyHandler()

    controller = Controller(
        handler,
        hostname=config['listen_host'],
        port=config['listen_port'],
        auth_required=True,  # Force use of our authenticator
        auth_require_tls=False,
        authenticator=authenticator_callback
    )

    logger.info(f"Starting SMTP proxy on {config['listen_host']}:{config['listen_port']}")
    logger.info(f"Forwarding to {config['forward_host']}:{config['forward_port']}")

    controller.start()

    try:
        # Keep the server running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down SMTP proxy")
    finally:
        controller.stop()


if __name__ == "__main__":
    asyncio.run(main())
