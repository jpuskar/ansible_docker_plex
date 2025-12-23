import asyncio
import logging
import yaml
import base64
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import SMTP as SMTPServer, AuthResult

import numpy as np


from SMTPProxyHandler import SMTPProxyHandler


# Load configuration
with open('/config/config.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Setup logging
logging.basicConfig(
    level=getattr(logging, config['log_level']),
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
    handler = SMTPProxyHandler(
        forward_host=config['forward_host'],
        forward_port=config['forward_port'],
        fallback_subject=config['fallback_subject'],
        filter_keywords=config['filter_keywords'],
        ai_detection_enabled=config['ai_detection_enabled'],
        confidence_threshold=config['confidence_threshold'],
    )

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
