"""Local authentication and CSRF helpers, with no replicated credentials."""

import base64
import hashlib
import hmac
import secrets

ITERATIONS = 600_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return a salted PBKDF2 hash compatible with the synthetic seed.

    :param password: Plain text supplied only to the local authenticator.
    :param salt: Optional salt for deterministic tests; normally random.
    :return: Algorithm, iteration count, salt and digest; never plain text.
    """
    salt = salt or secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "$".join(("pbkdf2_sha256", str(ITERATIONS), base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))


def verify_password(password: str, encoded: str) -> bool:
    """Compare a supplied password with an encoded local hash in constant time."""
    try:
        kind, iterations, salt, digest = encoded.split("$")
        count = int(iterations)
        if kind != "pbkdf2_sha256" or not 100_000 <= count <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt, validate=True), count)
        return hmac.compare_digest(actual, base64.b64decode(digest, validate=True))
    except (ValueError, TypeError):
        return False


def csrf_token(session: dict) -> str:
    """Get a session-bound token, creating it on the first page view."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def valid_csrf(session: dict, supplied: str | None) -> bool:
    """Validate a form/header token without accepting an absent session token."""
    return bool(supplied and session.get("csrf") and hmac.compare_digest(session["csrf"], supplied))

