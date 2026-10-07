"""Validated configuration for one deployment of the application."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """Environment settings that never contain a list of regional hosts.

    :param database_url: The only database connection string of this instance.
    :param node_region: Logical owner: 0 for the centre, 1 or 2 for a region.
    :param session_secret: Random signing key, unique to the instance.
    :param secure_cookies: Enable Secure cookies behind HTTPS.
    """

    database_url: str
    node_region: int
    session_secret: str
    secure_cookies: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        """Read and validate configuration, refusing insecure default keys."""
        database_url = os.environ.get("DATABASE_URL", "")
        secret = os.environ.get("SESSION_SECRET", "")
        region = int(os.environ.get("NODE_REGION", "1"))
        if not database_url:
            raise ValueError("DATABASE_URL is required")
        if region not in (0, 1, 2):
            raise ValueError("NODE_REGION must be 0, 1 or 2")
        if len(secret) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 characters")
        return cls(database_url, region, secret, os.environ.get("SECURE_COOKIES") == "1")

