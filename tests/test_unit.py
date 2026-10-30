"""Tests that need no database."""

from decimal import Decimal

import httpx

from app.queries import like_pattern, size_sort_key, unique
from notifier.check_alerts import format_inr, format_message, send_alert


def test_size_sort_key_clothing_order():
    sizes = ["XL", "32", "ONE SIZE", "S", "CUSTOMISE SIZE", "28", "M", "XXL", "XS"]
    assert sorted(sizes, key=size_sort_key) == [
        "XS", "S", "M", "XL", "XXL", "28", "32", "ONE SIZE", "CUSTOMISE SIZE",
    ]


def test_unique_keeps_first_position():
    assert unique(["M", "L", "M", "S", "L"]) == ["M", "L", "S"]


def test_like_pattern_escapes_wildcards():
    assert like_pattern("tee") == "%tee%"
    assert like_pattern("50%_off") == "%50\\%\\_off%"


def test_format_inr():
    assert format_inr(Decimal("1299.00")) == "₹1,299"
    assert format_inr(Decimal("160000.00")) == "₹160,000"
    assert format_inr(Decimal("999.50")) == "₹999.50"


ALERT = {
    "alert_id": 7,
    "title": "Tom & Jerry <Tee>",
    "brand_name": "Genrage",
    "size_norm": "M",
    "variant_title": "M / Black",
    "price": Decimal("999.00"),
    "compare_at_price": Decimal("1499.00"),
    "discount_pct": Decimal("33.4"),
    "image_url": "https://cdn.shopify.com/a.jpg",
    "url": "https://genrage.com/products/tom-jerry",
}


def test_format_message_escapes_html_and_shows_sale():
    text = format_message(ALERT)
    assert "Tom &amp; Jerry &lt;Tee&gt;" in text
    assert "Size: M" in text
    assert "₹999 <s>₹1,499</s> (-33%)" in text
    assert 'href="https://genrage.com/products/tom-jerry"' in text


def test_format_message_no_sale_no_size():
    text = format_message({**ALERT, "compare_at_price": None, "discount_pct": None,
                           "size_norm": None, "variant_title": "Default Title"})
    assert "Price: ₹999\n" in text
    assert "Size: Default Title" in text


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_send_alert_uses_photo():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    send_alert(_client(handler), "TOKEN", "42", ALERT)
    assert calls == ["/botTOKEN/sendPhoto"]


def test_send_alert_falls_back_to_text_when_photo_fails():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        status = 400 if request.url.path.endswith("sendPhoto") else 200
        return httpx.Response(status, json={})

    send_alert(_client(handler), "TOKEN", "42", ALERT)
    assert calls == ["/botTOKEN/sendPhoto", "/botTOKEN/sendMessage"]


def test_send_alert_raises_when_telegram_fails():
    def handler(request):
        return httpx.Response(500)

    try:
        send_alert(_client(handler), "TOKEN", "42", {**ALERT, "image_url": None})
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected an error")
