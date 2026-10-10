"""Security regression tests for dynamic SQL and PostgreSQL attachment settings."""

from pathlib import Path

import pytest

from core.database import DuckDBAnalytics
from core.sql_safety import quote_identifier, sql_string_literal, validate_postgres_connection


def test_csv_path_is_bound_as_a_value(tmp_path: Path):
    db = DuckDBAnalytics(in_memory=True)
    csv_path = tmp_path / "prices ' ; DROP TABLE llm_requests;--.csv"
    csv_path.write_text(
        "model,provider,input_usd_per_1m,output_usd_per_1m,cached_usd_per_1m,effective_from,effective_to\n"
        "test-model,openai,1.0,2.0,0.5,2026-01-01,\n",
        encoding="utf-8",
    )
    try:
        assert db.load_pricing_csv(csv_path) == 1
        assert db.conn.execute("SELECT model FROM model_pricing").fetchone()[0] == "test-model"
        assert db.conn.execute("SELECT COUNT(*) FROM llm_requests").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize("alias", ["x; DROP TABLE llm_requests;--", "has space", "", "a" * 64])
def test_postgres_alias_rejects_sql_fragments(alias: str):
    with pytest.raises(ValueError):
        quote_identifier(alias)


def test_postgres_alias_is_quoted():
    assert quote_identifier("pg_analytics") == '"pg_analytics"'


def test_sql_literal_escapes_quotes_and_rejects_controls():
    assert sql_string_literal("pa'ss") == "'pa''ss'"
    with pytest.raises(ValueError, match="control"):
        sql_string_literal("bad\nvalue")


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer", "require", "verify-ca"])
def test_remote_postgres_requires_full_tls_verification(mode: str):
    with pytest.raises(ValueError, match="verify-full"):
        validate_postgres_connection("db.example.com", 5432, "tokenlens", "tokenlens_writer", "pw", mode)


def test_loopback_postgres_allows_local_ssl_mode():
    assert validate_postgres_connection("127.0.0.1", 5432, "tokenlens", "writer", None, "prefer") == (5432, "prefer")


def test_remote_postgres_accepts_fully_verified_tls():
    assert validate_postgres_connection(
        "db.example.com", 5432, "tokenlens", "writer", "pw", "verify-full"
    ) == (5432, "verify-full")


def test_postgres_host_rejects_bracketed_ip_notation():
    with pytest.raises(ValueError, match="hostname or IP address"):
        validate_postgres_connection("[::1]", 5432, "tokenlens", "writer", None, "prefer")


class RecordingConnection:
    def __init__(self):
        self.queries: list[str] = []

    def execute(self, query: str, *args):
        self.queries.append(query)
        return self


def test_attach_uses_secret_and_keeps_password_out_of_attach_statement():
    db = DuckDBAnalytics.__new__(DuckDBAnalytics)
    db.conn = RecordingConnection()
    password = "sensitive ' password"

    assert db.attach_postgres(
        host="127.0.0.1",
        dbname="tokenlens",
        user="tokenlens_writer",
        password=password,
        pg_schema_alias="pg_analytics",
    )

    secret_query = next(query for query in db.conn.queries if "CREATE SECRET" in query)
    attach_query = next(query for query in db.conn.queries if "ATTACH" in query)
    assert "sensitive '' password" in secret_query
    assert password not in attach_query
    assert 'SECRET "tokenlens_pg_' in attach_query
    assert 'AS "pg_analytics"' in attach_query


def test_invalid_alias_rejected_before_extension_install():
    db = DuckDBAnalytics.__new__(DuckDBAnalytics)
    db.conn = RecordingConnection()
    with pytest.raises(ValueError):
        db.attach_postgres(user="writer", pg_schema_alias="pg; DROP TABLE llm_requests")
    assert db.conn.queries == []


def test_secret_creation_error_does_not_expose_password():
    class FailingConnection(RecordingConnection):
        def execute(self, query: str, *args):
            self.queries.append(query)
            if "CREATE SECRET" in query:
                raise RuntimeError(f"SQL failed: {query}")
            return self

    db = DuckDBAnalytics.__new__(DuckDBAnalytics)
    db.conn = FailingConnection()
    password = "must-not-leak"
    with pytest.raises(RuntimeError) as error:
        db.attach_postgres(user="writer", password=password)
    assert password not in str(error.value)
