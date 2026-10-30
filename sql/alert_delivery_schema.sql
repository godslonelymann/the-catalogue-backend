-- Apply as admin to the catalogue database after app_schema.sql.
-- One delivery per alert, across all workflow runs and manual invocations.
CREATE TABLE IF NOT EXISTS app.alert_deliveries (
    alert_id BIGINT PRIMARY KEY REFERENCES app.alerts (id),
    run_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('sending', 'sent', 'failed', 'uncertain')),
    attempts INTEGER NOT NULL DEFAULT 1,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    telegram_message_id BIGINT,
    error TEXT
);
GRANT SELECT, INSERT, UPDATE ON app.alert_deliveries TO app_user;
