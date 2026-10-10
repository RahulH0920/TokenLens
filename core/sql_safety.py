"""Small SQL construction helpers for the DuckDB/PostgreSQL boundary."""

from __future__ import annotations

import ipaddress
import re
import unicodedata


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
_HOSTNAME = re.compile(r"[A-Za-z0-9.-]{1,253}\Z")
_POSTGRES_SSLMODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def quote_identifier(identifier: str) -> str:
    """Validate and quote a simple SQL identifier (for example, an attached DB alias)."""
    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise ValueError("SQL identifier must use 1-63 ASCII letters, digits, or underscores and start with a letter or underscore.")
    return f'"{identifier}"'


def sql_string_literal(value: str, *, field: str = "SQL value", max_length: int = 4096) -> str:
    """Render validated text as an escaped SQL string literal."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    if not value or len(value) > max_length:
        raise ValueError(f"{field} must contain between 1 and {max_length} characters.")
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError(f"{field} must not contain control characters.")
    return "'" + value.replace("'", "''") + "'"


def validate_postgres_connection(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str | None,
    sslmode: str,
) -> tuple[int, str]:
    """Validate PostgreSQL connection fields and require full TLS verification remotely."""
    if not isinstance(host, str) or not host or host != host.strip() or len(host) > 253:
        raise ValueError("PostgreSQL host must be a non-empty hostname or IP address without surrounding whitespace.")
    if any(unicodedata.category(char) == "Cc" for char in host):
        raise ValueError("PostgreSQL host must not contain control characters.")

    # Accept standard hostnames and IP addresses only; this excludes libpq service
    # strings and socket paths from this API's limited connection surface.
    try:
        address = ipaddress.ip_address(host)
        is_loopback = address.is_loopback
    except ValueError:
        if not _HOSTNAME.fullmatch(host):
            raise ValueError("PostgreSQL host must be a hostname or IP address.") from None
        is_loopback = host.rstrip(".").lower() == "localhost"

    if isinstance(port, bool):
        raise ValueError("PostgreSQL port must be an integer between 1 and 65535.")
    try:
        normalized_port = int(port)
    except (TypeError, ValueError):
        raise ValueError("PostgreSQL port must be an integer between 1 and 65535.") from None
    if str(normalized_port) != str(port) or not 1 <= normalized_port <= 65535:
        raise ValueError("PostgreSQL port must be an integer between 1 and 65535.")

    sslmode = sslmode.lower() if isinstance(sslmode, str) else ""
    if sslmode not in _POSTGRES_SSLMODES:
        raise ValueError("PostgreSQL sslmode is invalid.")
    if not is_loopback and sslmode != "verify-full":
        raise ValueError("Remote PostgreSQL connections require sslmode='verify-full'.")

    sql_string_literal(host, field="PostgreSQL host", max_length=253)
    sql_string_literal(dbname, field="PostgreSQL database name", max_length=63)
    sql_string_literal(user, field="PostgreSQL user", max_length=63)
    if password is not None:
        # Empty passwords can be valid with a local peer/trust configuration.
        if not isinstance(password, str):
            raise ValueError("PostgreSQL password must be text.")
        if len(password) > 4096 or any(unicodedata.category(char) == "Cc" for char in password):
            raise ValueError("PostgreSQL password is too long or contains control characters.")
    return normalized_port, sslmode
