-- API Pulse: PostgreSQL schema
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE roles (
    role_id    SERIAL PRIMARY KEY,
    role_name  VARCHAR(20) NOT NULL UNIQUE
               CHECK (role_name IN ('admin', 'user'))
);

CREATE TABLE users (
    user_id        SERIAL PRIMARY KEY,
    name           VARCHAR(100) NOT NULL,
    email          VARCHAR(150) NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role_id        INT NOT NULL REFERENCES roles(role_id),
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE plans (
    plan_id                 SERIAL PRIMARY KEY,
    plan_name               VARCHAR(50) NOT NULL UNIQUE,
    max_requests_per_month  INT NOT NULL CHECK (max_requests_per_month > 0),
    max_services            INT NOT NULL CHECK (max_services > 0),
    price                   NUMERIC(8,2) NOT NULL DEFAULT 0 CHECK (price >= 0)
);

CREATE TABLE user_subscriptions (
    subscription_id  SERIAL PRIMARY KEY,
    user_id          INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    plan_id          INT NOT NULL REFERENCES plans(plan_id),
    start_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    end_date         DATE,
    status           VARCHAR(20) NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'expired', 'cancelled')),
    CHECK (end_date IS NULL OR end_date >= start_date)
);

CREATE TABLE api_services (
    service_id    SERIAL PRIMARY KEY,
    user_id       INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    service_name  VARCHAR(100) NOT NULL,
    provider      VARCHAR(100) NOT NULL,
    base_url      VARCHAR(255) NOT NULL,
    description   TEXT,
    embedding     vector(384),
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, service_name)
);

CREATE TABLE api_credentials (
    credential_id  SERIAL PRIMARY KEY,
    service_id     INT NOT NULL REFERENCES api_services(service_id) ON DELETE CASCADE,
    encrypted_key  TEXT NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMP
);

CREATE TABLE api_endpoints (
    endpoint_id  SERIAL PRIMARY KEY,
    service_id   INT NOT NULL REFERENCES api_services(service_id) ON DELETE CASCADE,
    path         VARCHAR(255) NOT NULL,
    http_method  VARCHAR(10) NOT NULL
                 CHECK (http_method IN ('GET','POST','PUT','PATCH','DELETE')),
    UNIQUE (service_id, path, http_method)
);

CREATE TABLE usage_limits (
    limit_id      SERIAL PRIMARY KEY,
    service_id    INT NOT NULL REFERENCES api_services(service_id) ON DELETE CASCADE,
    limit_type    VARCHAR(10) NOT NULL CHECK (limit_type IN ('daily', 'monthly')),
    max_requests  INT NOT NULL CHECK (max_requests > 0),
    UNIQUE (service_id, limit_type)
);

CREATE TABLE alerts (
    alert_id    SERIAL PRIMARY KEY,
    service_id  INT NOT NULL REFERENCES api_services(service_id) ON DELETE CASCADE,
    metric      VARCHAR(20) NOT NULL
                CHECK (metric IN ('usage_percent', 'error_rate', 'latency')),
    threshold   NUMERIC(8,2) NOT NULL CHECK (threshold > 0),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE alert_events (
    event_id      SERIAL PRIMARY KEY,
    alert_id      INT NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
    triggered_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    message       TEXT NOT NULL
);

-- Indexes on foreign keys and common lookups
CREATE INDEX idx_subscriptions_user  ON user_subscriptions(user_id);
CREATE INDEX idx_services_user       ON api_services(user_id);
CREATE INDEX idx_credentials_service ON api_credentials(service_id);
CREATE INDEX idx_endpoints_service   ON api_endpoints(service_id);
CREATE INDEX idx_alerts_service      ON alerts(service_id);
CREATE INDEX idx_events_alert_time   ON alert_events(alert_id, triggered_at DESC);

-- Vector index for semantic search (cosine distance)
CREATE INDEX idx_services_embedding
    ON api_services USING hnsw (embedding vector_cosine_ops);

-- Starter data
INSERT INTO roles (role_name) VALUES ('admin'), ('user');

INSERT INTO plans (plan_name, max_requests_per_month, max_services, price) VALUES
    ('Free',       1000,    2,  0.00),
    ('Pro',        100000,  10, 9.99),
    ('Enterprise', 1000000, 50, 49.99);