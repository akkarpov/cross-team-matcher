"""Single-DSN database access with isolated transactions and actor context.

Physical placement belongs to PostgreSQL metadata. This module deliberately
knows only logical relations, not regional hosts or physical table schemas.
"""

from contextlib import contextmanager
from typing import Iterator
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.errors import DomainError, MESSAGES


class Database:
    """Connection factory bound to exactly one PostgreSQL node.

    :param dsn: Local connection string.
    :ivar dsn: Immutable-by-convention connection configuration.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        """Open one transaction; commit on success, roll back on any exception.

        :yield: A dictionary-row PostgreSQL connection with an eight-second
            statement timeout and a three-second connection timeout.
        """
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=3,
                             options="-c statement_timeout=8000 -c lock_timeout=5000") as connection:
            yield connection


class ActorDatabase(Database):
    """A Database whose transactions carry a server-authenticated identity.

    The caller obtains ``actor_id`` from a signed local session, never from
    browser-supplied JSON. SQL independently enforces the account's roles.

    :param dsn: The instance's single connection string.
    :param actor_id: UUID of an active local account.
    """

    def __init__(self, dsn: str, actor_id: str):
        super().__init__(dsn)
        self.actor_id = actor_id

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        """Install transaction-local identity before running a query."""
        with super().connect() as connection:
            connection.execute("SELECT set_config('app.actor_id', %s, true)", (self.actor_id,))
            yield connection

    def mutate(self, action: str, payload: dict) -> dict:
        """Run a local business operation through the stable SQL contract.

        :param action: Documented operation, e.g. ``invitation.respond``.
        :param payload: Validated form values, without identity overrides.
        :return: Committed JSON result from the business operation.
        :raises DomainError: A rejected business rule or unavailable owner.
        """
        for attempt in range(3):
            try:
                with self.connect() as connection:
                    result = connection.execute("SELECT global.mutate(%s, %s::jsonb) AS result", (action, Jsonb(payload))).fetchone()["result"]
                # A recorded refusal is a committed workflow result, not a
                # reason to roll back its receipt or historical event.
                if isinstance(result, dict) and result.get("error"):
                    code = result["error"] if isinstance(result["error"], str) else result["error"].get("code")
                    raise DomainError(code if code in MESSAGES else "payload_invalid", 409)
                return result
            except psycopg.errors.SerializationFailure:
                if attempt == 2:
                    raise DomainError("version_conflict", 409) from None
            except psycopg.Error as exc:
                raise database_error(exc) from None
        raise DomainError("version_conflict", 409)


def database_error(exc: psycopg.Error) -> DomainError:
    """Map PostgreSQL errors to safe contract codes without leaking SQL."""
    message = (exc.diag.message_primary or "") if exc.diag else ""
    for code in MESSAGES:
        if code in message:
            return DomainError(code, 403 if code == "forbidden" else 409)
    if exc.sqlstate == "42501":
        return DomainError("forbidden", 403)
    if exc.sqlstate == "P0002":
        return DomainError("not_found", 404)
    if isinstance(exc, (psycopg.OperationalError, psycopg.errors.QueryCanceled)):
        return DomainError("owner_unavailable", 503)
    if isinstance(exc, psycopg.IntegrityError) or (exc.sqlstate or "").startswith("22"):
        return DomainError("payload_invalid", 422)
    return DomainError("payload_invalid", 400)
