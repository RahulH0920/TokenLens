"""Verify a read-only DuckDB connection to a PostgreSQL server.

Connection settings are read from libpq environment variables. No credentials
are accepted on the command line or printed by this script.
"""

from __future__ import annotations

import os
import sys

import duckdb


REQUIRED = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def main() -> int:
    missing = [key for key in REQUIRED if not os.getenv(key)]
    if missing:
        print("Missing PostgreSQL settings: " + ", ".join(missing), file=sys.stderr)
        print("Set these in your local environment; do not put passwords in source or chat.", file=sys.stderr)
        return 2

    host = os.environ["PGHOST"].strip().lower()
    sslmode = os.getenv("PGSSLMODE", "").strip().lower()
    if host not in LOOPBACK_HOSTS and sslmode != "verify-full":
        print(
            "Remote PostgreSQL connections require PGSSLMODE=verify-full and a trusted server certificate.",
            file=sys.stderr,
        )
        return 2

    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("LOAD postgres")
        connection.execute("ATTACH '' AS finops_pg (TYPE postgres, READ_ONLY)")
        connection.execute("SELECT 1 FROM finops_pg.pg_catalog.pg_database LIMIT 1").fetchone()
        print("DuckDB connected to PostgreSQL successfully (read-only).")
        return 0
    except Exception as exc:
        # Do not echo the connection string or environment; driver errors can
        # contain host details and should be reviewed before sharing.
        print(f"PostgreSQL connection failed ({type(exc).__name__}). Check host, TLS, credentials, and firewall.", file=sys.stderr)
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
