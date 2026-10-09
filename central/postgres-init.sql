-- Provision the schema as the PostgreSQL bootstrap administrator, but give
-- the receiver only INSERT plus the SELECT columns required by ON CONFLICT
-- and RETURNING. The application password comes from the container env.
\getenv ingest_password CENTRAL_DB_PASSWORD
CREATE ROLE tokenlens_ingest LOGIN PASSWORD :'ingest_password';
CREATE SCHEMA tokenlens;
ALTER ROLE tokenlens_ingest SET search_path TO tokenlens, public;

CREATE TABLE tokenlens.usage_events (
    host_id VARCHAR(63) NOT NULL,
    request_id VARCHAR(200) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    provider_host VARCHAR(255) NOT NULL,
    provider_request_id VARCHAR(200),
    model VARCHAR(200) NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    team VARCHAR(200) NOT NULL,
    feature VARCHAR(200) NOT NULL,
    user_id VARCHAR(200) NOT NULL,
    env VARCHAR(100) NOT NULL,
    input_tokens BIGINT NOT NULL CHECK (input_tokens >= 0),
    output_tokens BIGINT NOT NULL CHECK (output_tokens >= 0),
    cached_tokens BIGINT NOT NULL CHECK (cached_tokens >= 0 AND cached_tokens <= input_tokens),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    status VARCHAR(50) NOT NULL,
    source VARCHAR(50) NOT NULL,
    input_cost_usd NUMERIC(18, 6) NOT NULL,
    output_cost_usd NUMERIC(18, 6) NOT NULL,
    cached_cost_usd NUMERIC(18, 6) NOT NULL,
    total_cost_usd NUMERIC(18, 6) NOT NULL,
    missing_price BOOLEAN NOT NULL,
    is_unattributed BOOLEAN NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (host_id, request_id)
);
CREATE INDEX usage_events_host_time_idx ON tokenlens.usage_events (host_id, timestamp_utc DESC);
CREATE INDEX usage_events_provider_time_idx ON tokenlens.usage_events (provider, timestamp_utc DESC);
GRANT USAGE ON SCHEMA tokenlens TO tokenlens_ingest;
GRANT INSERT ON tokenlens.usage_events TO tokenlens_ingest;
GRANT SELECT (host_id, request_id) ON tokenlens.usage_events TO tokenlens_ingest;
