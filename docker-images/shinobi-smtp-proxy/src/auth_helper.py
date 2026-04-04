import base64
import logging

from aiosmtpd.smtp import AuthResult


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

                    logger.info(f"LOGIN auth - Username: {username}, Password: {'*' * 8}")
                    return AuthResult(success=True)
                elif isinstance(auth_data, dict) and 'username' in auth_data and 'password' in auth_data:
                    # Fallback for dict format
                    username = auth_data['username']
                    password = auth_data['password']

                    session.username = username
                    session.password = password

                    logger.info(f"LOGIN auth (dict) - Username: {username}, Password: {'*' * 8}")
                    return AuthResult(success=True)
                else:
                    logger.error(f"LOGIN auth failed - invalid auth_data format: {auth_data}")
                    logger.error(f"auth_data type: {type(auth_data)}, dir: {dir(auth_data)}")
                    return AuthResult(success=False, message="Invalid credentials format")
            except Exception as login_err:
                logger.error(f"LOGIN auth processing error: {login_err}")
                return AuthResult(success=False, message="Login processing error")

        elif mechanism == 'PLAIN':
            # In newer aiosmtpd, auth_data is a LoginPassword namedtuple
            try:
                if hasattr(auth_data, 'login') and hasattr(auth_data, 'password'):
                    username = auth_data.login
                    password = auth_data.password

                    if isinstance(username, bytes):
                        username = username.decode('utf-8')
                    if isinstance(password, bytes):
                        password = password.decode('utf-8')

                    session.username = username
                    session.password = password

                    logger.info(f"PLAIN auth - Username: {username}, Password: {'*' * 8}")
                    return AuthResult(success=True)
                elif isinstance(auth_data, str):
                    # Legacy fallback: raw PLAIN string
                    try:
                        decoded = base64.b64decode(auth_data).decode('utf-8')
                    except Exception:
                        decoded = auth_data

                    parts = decoded.split('\x00')
                    if len(parts) >= 3:
                        username = parts[1]
                        password = parts[2]
                        session.username = username
                        session.password = password
                        logger.info(f"PLAIN auth (legacy) - Username: {username}, Password: {'*' * 8}")
                        return AuthResult(success=True)
                    else:
                        logger.error(f"PLAIN auth failed - insufficient parts")
                        return AuthResult(success=False, message="Invalid PLAIN format")
                else:
                    logger.error(f"PLAIN auth failed - unexpected auth_data type: {type(auth_data)}")
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
