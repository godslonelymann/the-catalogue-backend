"""Restock alert checker: python -m notifier.check_alerts

Called by Airflow after successful brand saves (CLI retained for maintenance):
1. Find active alerts whose variant is now in stock.
2. Send one Telegram message per alert.
3. Persist delivery status and mark the alert fired.
Alerts are only created on sold-out variants, so "active + now available" means restocked.
"""

import html
import logging
import sys
import time
import uuid
from decimal import Decimal

import httpx
import psycopg
from psycopg.rows import dict_row

from app.config import load_settings

log = logging.getLogger("notifier")

RESTOCKED = """
SELECT a.id AS alert_id, p.title, b.name AS brand_name,
       v.size_norm, v.title AS variant_title,
       v.price, v.compare_at_price, v.discount_pct,
       p.image_urls[1] AS image_url,
       b.base_url || '/products/' || p.handle AS url
FROM app.alerts a
JOIN catalog.variants v ON v.id = a.variant_id
JOIN catalog.products p ON p.id = v.product_id
JOIN catalog.brands b ON b.id = p.brand_id
WHERE a.status = 'active' AND v.available AND v.is_active AND p.is_active
ORDER BY a.id
"""

TELEGRAM_CAPTION_LIMIT = 1024
SENDER_LOCK = 730291846  # shared by Airflow summaries and standalone notifier calls


def format_inr(amount: Decimal) -> str:
    """Decimal('1299.00') -> '₹1,299'; Decimal('1299.50') -> '₹1,299.50'."""
    if amount == amount.to_integral_value():
        return f"₹{amount:,.0f}"
    return f"₹{amount:,.2f}"


def format_message(alert: dict) -> str:
    """Telegram message (HTML) for one restocked variant."""
    size = alert["size_norm"] or alert["variant_title"] or "-"
    price = format_inr(alert["price"])
    if alert["compare_at_price"] is not None:
        price += f" <s>{format_inr(alert['compare_at_price'])}</s> (-{alert['discount_pct']:.0f}%)"
    return "\n".join([
        "🔔 <b>Back in stock</b>",
        f"<b>{html.escape(alert['title'])}</b>",
        f"Brand: {html.escape(alert['brand_name'])}",
        f"Size: {html.escape(size)}",
        f"Price: {price}",
        f'<a href="{html.escape(alert["url"], quote=True)}">Open in store</a>',
    ])


class DeliveryUncertain(Exception):
    """Telegram may have accepted the message; automatic retry could duplicate it."""


class TelegramRejected(httpx.HTTPStatusError):
    """Telegram explicitly rejected a message, including an HTTP 200 / ok=false."""


def accepted_message(response: httpx.Response) -> int | None:
    response.raise_for_status()
    try:
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
            raise DeliveryUncertain("Telegram response has no delivery acknowledgement")
    except ValueError as error:
        raise DeliveryUncertain("Telegram response is not JSON") from error
    if not body["ok"]:
        raise TelegramRejected("Telegram rejected the message", request=response.request, response=response)
    return (body.get("result") or {}).get("message_id")


def send_alert(client: httpx.Client, token: str, chat_id: str, alert: dict) -> int | None:
    """Only a definite photo rejection permits a second request as text."""
    # Airflow logs at INFO; httpx's request log would expose the bot token in URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    text = format_message(alert)
    base = f"https://api.telegram.org/bot{token}"
    if alert["image_url"] and len(text) <= TELEGRAM_CAPTION_LIMIT:
        response = client.post(f"{base}/sendPhoto", json={
            "chat_id": chat_id, "photo": alert["image_url"], "caption": text, "parse_mode": "HTML",
        })
        if response.status_code != 400:
            return accepted_message(response)
        log.warning("alert %s: photo rejected; sending text", alert["alert_id"])
    response = client.post(f"{base}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
    })
    return accepted_message(response)


def delivery_status(error: Exception) -> str:
    if isinstance(error, TelegramRejected):
        return "failed"
    if isinstance(error, httpx.HTTPStatusError) and 400 <= error.response.status_code < 500:
        return "failed"
    # Timeouts, transport errors, malformed acknowledgements, and server errors
    # can all happen after acceptance. Preserve evidence and require review.
    return "uncertain"


def deliver_alerts(database_url: str, token: str | None, chat_id: str | None,
                   *, brand_ids: list[int] | None = None, run_id: str | None = None) -> dict:
    """One sender for all callers. An empty brand scope sends nothing.

    Failed deliveries may retry. Sent/uncertain deliveries never automatically resend.
    A process killed after claiming leaves 'sending', recovered as 'uncertain'.
    """
    counts = {"sent": 0, "failed": 0, "uncertain": 0}
    run_id = run_id or f"manual__{uuid.uuid4()}"
    if brand_ids == []:
        return counts
    if not (token and chat_id):
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as conn, \
            httpx.Client(timeout=30) as client:
        locked = conn.execute("SELECT pg_try_advisory_lock(%s) AS locked", (SENDER_LOCK,)).fetchone()["locked"]
        if not locked:
            raise RuntimeError("another Telegram sender is running")
        # This lock proves there is no live sending attempt in another process.
        conn.execute("UPDATE app.alert_deliveries SET status = 'uncertain', "
                     "error = 'sender stopped before acknowledgement was recorded' WHERE status = 'sending'")
        query = RESTOCKED
        params = ()
        if brand_ids is not None:
            query = query.replace("ORDER BY a.id", "AND b.id = ANY(%s::integer[]) ORDER BY a.id")
            params = (brand_ids,)
        alerts = conn.execute(query, params).fetchall()
        for alert in alerts:
            with conn.transaction():
                active = conn.execute("SELECT status FROM app.alerts WHERE id = %s FOR UPDATE",
                                      (alert["alert_id"],)).fetchone()
                if active["status"] != "active":
                    continue
                claimed = conn.execute(
                    "INSERT INTO app.alert_deliveries (alert_id, run_id, status) VALUES (%s, %s, 'sending') "
                    "ON CONFLICT (alert_id) DO UPDATE SET status = 'sending', run_id = EXCLUDED.run_id, "
                    "attempts = app.alert_deliveries.attempts + 1, attempted_at = now(), "
                    "completed_at = NULL, error = NULL "
                    "WHERE app.alert_deliveries.status = 'failed' RETURNING alert_id",
                    (alert["alert_id"], run_id),
                ).fetchone()
                if not claimed:
                    previous = conn.execute("SELECT status FROM app.alert_deliveries WHERE alert_id = %s",
                                            (alert["alert_id"],)).fetchone()["status"]
                    if previous == "uncertain":
                        counts["uncertain"] += 1
                    continue
            # The claim is committed before the request. A crash cannot silently retry it.
            try:
                message_id = send_alert(client, token, chat_id, alert)
            except (httpx.HTTPError, DeliveryUncertain) as error:
                status = delivery_status(error)
                conn.execute("UPDATE app.alert_deliveries SET status = %s, error = %s, completed_at = now() "
                             "WHERE alert_id = %s", (status, type(error).__name__, alert["alert_id"]))
                counts[status] += 1
                log.error("alert %s: %s (%s)", alert["alert_id"], status, type(error).__name__)
            else:
                with conn.transaction():
                    conn.execute("UPDATE app.alert_deliveries SET status = 'sent', telegram_message_id = %s, "
                                 "completed_at = now() WHERE alert_id = %s", (message_id, alert["alert_id"]))
                    conn.execute("UPDATE app.alerts SET status = 'fired', last_fired_at = now() "
                                 "WHERE id = %s AND status = 'active'", (alert["alert_id"],))
                counts["sent"] += 1
                log.info("alert %s sent", alert["alert_id"])
            time.sleep(1)
        # A retry must report earlier accepted messages from this run too.
        counts["sent"] = conn.execute(
            "SELECT count(*) AS sent FROM app.alert_deliveries WHERE run_id = %s AND status = 'sent'",
            (run_id,),
        ).fetchone()["sent"]
    return counts


def main() -> int:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    started = time.monotonic()
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        return 1

    outcome = deliver_alerts(settings.database_url, settings.telegram_bot_token, settings.telegram_chat_id)
    log.info("done: %s (%.1f s)", outcome, time.monotonic() - started)
    return 0 if not (outcome["failed"] or outcome["uncertain"]) else 1


if __name__ == "__main__":
    sys.exit(main())
