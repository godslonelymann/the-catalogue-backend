# the-catalogue-backend

FastAPI API for The Catalogue, plus the restock alert checker (Telegram).
It reads `catalog.*` (written by the pipeline) and owns `app.*` (wishlist, alerts).

```
Browser ──HTTPS──► Caddy :443 ──► uvicorn 127.0.0.1:8000 ──► Postgres (as app_user)
Airflow after brand saves ──► notifier/check_alerts.py ──► Telegram
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in DATABASE_URL, TELEGRAM_*, CORS_ORIGINS
```

## Database (once, as admin, after the pipeline's catalog_schema.sql)

```bash
psql -h localhost -U postgres -d brands -f sql/app_schema.sql
psql -h localhost -U postgres -d brands -f sql/alert_delivery_schema.sql
psql -h localhost -U postgres -d brands -c "ALTER ROLE app_user LOGIN PASSWORD 'change-me'"
```

## Run locally

```bash
uvicorn app.main:app --reload --port 8000
# docs: http://localhost:8000/docs
```

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Checks the database too |
| GET | `/brands` | Brands with product counts (for tabs) |
| GET | `/brands/{id}/products` | `size, category, min_price, max_price, in_stock (default true), on_sale, q, sort, page, page_size` |
| GET | `/brands/{id}/filters` | Sizes, categories and price range for the filter controls |
| GET | `/products/{id}` | Detail with variants in store order, `wishlist_id`, `alert_id` per variant |
| GET | `/updates` | Products with valid changes in the last 7 days; `brand_id, change, page, page_size`; includes `last_checked_at` |
| GET / POST | `/wishlist` | POST `{product_id}`; adding twice returns the same item |
| DELETE | `/wishlist/{id}` | |
| GET / POST | `/alerts` | POST `{variant_id}`; **409** if the variant is in stock now |
| DELETE | `/alerts/{id}` | Sets status `cancelled` |

Gift cards and inactive products are hidden from listings. Money is sent as strings (`"1299.00"`).

Product cards include `recent_changes` and `last_seen_at`; detail includes the latest
50 recorded `changes`. Brand listings accept `change=all|new|price_drop|restock` and
`sort=recently_updated`. Wishlist accepts these change filters plus `change=removed`;
removed saved products remain visible beyond seven days. A price drop/restock badge
only appears while that variant is active, in stock, and (for price drops) still at
the observed lower price. When a size filter is present, it matches the variant that
changed. Filter options also include the brand's `last_checked_at`.

Apply the pipeline's updated `catalog_schema.sql` and run its history backfill before
restarting this backend. The backend remains read-only on all `catalog` tables.

## Alert checker

```bash
python -m notifier.check_alerts
```

Finds active alerts whose variant is back in stock, sends one Telegram message each
(photo + title, brand, size, price, link), then marks them `fired`.
Airflow calls `deliver_alerts(..., brand_ids=[...], run_id=...)` with only brands
successfully saved in the current workflow. An empty list sends nothing. The CLI
checks all brands and is retained for maintenance.

Apply `sql/alert_delivery_schema.sql` before deploying this notifier. It adds
`app.alert_deliveries` with delivery status, attempts, and Telegram message IDs.
All callers share a database sender lock. Confirmed rejection can retry; timeouts,
transport errors, HTTP 5xx, and interrupted sends are held as `uncertain` for
operator review. Photo fallback only follows an explicit HTTP 400 rejection.
See the pipeline's [Airflow deployment guide](../the-catalogue-pipeline/airflow/README.md)
for recovery and the cron switchover.

## Deploy on EC2

```bash
sudo cp deploy/the-catalogue-backend.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now the-catalogue-backend
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl restart caddy
```

Remove the legacy 03:00 notifier cron after the Airflow rehearsal passes. Airflow invokes it after successful brand saves; do not leave both schedules enabled.

Update: `git pull && sudo systemctl restart the-catalogue-backend`.

## Logs

| What | Where | Read it |
|---|---|---|
| API requests (uvicorn access log) | systemd journal | `journalctl -u the-catalogue-backend -f` |
| API actions: wishlist add/remove, alert create/cancel, 4xx reasons, slow requests (> 1 s), 500 errors | systemd journal | same |
| Alert checker: each alert sent, failures, run time | `logs/alerts.log` | `tail logs/alerts.log` |
| Public requests to the API | Caddy, `/var/log/caddy/access.log` (JSON) | `sudo tail -f /var/log/caddy/access.log` |

`LOG_LEVEL=DEBUG` in `.env` adds one line per request with its time (restart the service).
Rotation: the journal and Caddy rotate on their own. For `logs/alerts.log`:
`sudo cp deploy/the-catalogue-backend.logrotate /etc/logrotate.d/the-catalogue-backend`.

## Tests

```bash
pytest                                   # pure tests, no database
TEST_DATABASE_URL=postgresql://postgres:...@localhost:5432/catalogue_test pytest
```

The API tests need an **empty, throwaway** database and an admin user. They load the
catalog schema from `../the-catalogue-pipeline/sql/catalog_schema.sql` (or `CATALOG_SCHEMA_PATH`),
then call the API as `app_user`, so the grants are tested too. If `app_user` needs a
password there, set `TEST_APP_DATABASE_URL`.
