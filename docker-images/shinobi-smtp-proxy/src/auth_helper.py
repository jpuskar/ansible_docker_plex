from __future__ import annotations

import logging

from aiosmtpd.smtp import AuthResult, Envelope, LoginPassword, SMTP, Session

log = logging.getLogger("smtp-proxy")


def _extract_login_password(auth_data: LoginPassword) -> tuple[str, str]:
    """Pull username/password strings from a LoginPassword namedtuple."""
    username = auth_data.login
    password = auth_data.password
    if isinstance(username, bytes):
        username = username.decode("utf-8")
    if isinstance(password, bytes):
        password = password.decode("utf-8")
    return username, password


def authenticator(server: SMTP, session: Session, envelope: Envelope, mechanism: str, auth_data: LoginPassword) -> AuthResult:
    """aiosmtpd authenticator callback.

    Accepts LOGIN and PLAIN mechanisms. Stashes credentials on the session
    so the forwarder can relay them downstream.
    """
    try:
        if mechanism not in ("LOGIN", "PLAIN"):
            log.warning("Unsupported AUTH mechanism: %s", mechanism)
            return AuthResult(success=False, message="Unsupported mechanism")

        # aiosmtpd passes a LoginPassword namedtuple for both LOGIN and PLAIN
        if not hasattr(auth_data, "login"):
            log.error("Unexpected auth_data type: %s", type(auth_data).__name__)
            return AuthResult(success=False, message="Invalid credentials")

        username, password = _extract_login_password(auth_data)
        session.username = username
        session.password = password
        log.debug("AUTH %s succeeded for %s", mechanism, username)
        return AuthResult(success=True)

    except Exception:
        log.exception("Auth error")
        return AuthResult(success=False, message="Authentication error")
