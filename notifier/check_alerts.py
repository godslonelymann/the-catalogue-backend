"""Restock alert checker: python -m notifier.check_alerts

Runs by cron at 3:00 AM IST, after the pipeline:
1. Find active alerts whose variant is now in stock.
2. Send one Telegram message per alert.
3. Mark the alert fired.
Alerts are only created on sold-out variants, so "active + now available" means restocked.
"""

import html
import logging
import sys
import time
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


def send_alert(client: httpx.Client, token: str, chat_id: str, alert: dict) -> None:
    """Photo with caption when there is an image, else a text message. Raises on failure."""
    text = format_message(alert)
    base = f"https://api.telegram.org/bot{token}"
    if alert["image_url"] and len(text) <= TELEGRAM_CAPTION_LIMIT:
        response = client.post(f"{base}/sendPhoto", json={
            "chat_id": chat_id, "photo": alert["image_url"], "caption": text, "parse_mode": "HTML",
        })
        if response.status_code == 200:
            return
        # Telegram could not fetch the image: fall back to a text message.
        log.warning("alert %s: sendPhoto failed (HTTP %s), sending text", alert["alert_id"], response.status_code)
    response = client.post(f"{base}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML",
    })
    response.raise_for_status()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        log.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        return 1

    sent = failed = 0
    with psycopg.connect(settings.database_url, autocommit=True, row_factory=dict_row) as conn, \
            httpx.Client(timeout=30) as client:
        alerts = conn.execute(RESTOCKED).fetchall()
        log.info("%d restocked alert(s)", len(alerts))
        for alert in alerts:
            try:
                send_alert(client, settings.telegram_bot_token, settings.telegram_chat_id, alert)
            except httpx.HTTPError as e:
                # Do not log the exception text: the request URL contains the bot token.
                log.error("alert %s: not sent (%s), will retry next run", alert["alert_id"], type(e).__name__)
                failed += 1
                continue
            # Mark fired only after Telegram accepted the message.
            conn.execute(
                "UPDATE app.alerts SET status = 'fired', last_fired_at = now() "
                "WHERE id = %s AND status = 'active'",
                (alert["alert_id"],),
            )
            sent += 1
            time.sleep(1)  # Telegram allows about 1 message per second per chat

    log.info("done: %d sent, %d failed", sent, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
