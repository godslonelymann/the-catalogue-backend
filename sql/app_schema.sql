-- app schema: data the user creates on the website (wishlist, restock alerts).
-- Run as the admin user (postgres) on database `brands`, AFTER the pipeline's catalog_schema.sql:
--   psql -h localhost -U postgres -d brands -f sql/app_schema.sql
-- Safe to run more than once.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user NOLOGIN;  -- set a password: ALTER ROLE app_user LOGIN PASSWORD '...';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.wishlist (
    id          BIGSERIAL PRIMARY KEY,
    product_id  BIGINT NOT NULL UNIQUE REFERENCES catalog.products (id),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.alerts (
    id             BIGSERIAL PRIMARY KEY,
    variant_id     BIGINT NOT NULL REFERENCES catalog.variants (id),
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'fired', 'cancelled')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_fired_at  TIMESTAMPTZ
);

-- Only one active alert per variant.
CREATE UNIQUE INDEX IF NOT EXISTS alerts_one_active_per_variant
    ON app.alerts (variant_id) WHERE status = 'active';

-- Access: app_user reads and writes app. The pipeline user has no access to app.
GRANT USAGE ON SCHEMA app TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO app_user;
