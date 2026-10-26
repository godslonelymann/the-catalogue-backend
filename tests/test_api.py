"""API tests against a real Postgres (see conftest.py). Skipped without TEST_DATABASE_URL."""

import os
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.conftest import CATALOG_SCHEMA_PATH, ROOT, TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL or not CATALOG_SCHEMA_PATH.exists(),
    reason="TEST_DATABASE_URL not set or catalog_schema.sql not found",
)

# Brand 1: p1 tee (S in stock + on sale, M sold out, L in stock), p2 hoodie (sold out),
# p3 gift card, p4 inactive, p5 cargo pants (newest). Brand 2: one tee.
SEED = """
INSERT INTO catalog.brands (id, name, base_url) VALUES
    (1, 'Fugazee', 'https://www.fugazee.com'),
    (2, 'Genrage', 'https://genrage.com');

INSERT INTO catalog.products (id, brand_id, shopify_product_id, handle, title, category, tags,
                              image_urls, is_gift_card, is_active, shopify_created_at) VALUES
    (1, 1, 101, 'black-tee', 'Black Tee', 'tee', '{summer}', '{https://cdn.shopify.com/1.jpg}', false, true, '2026-01-01'),
    (2, 1, 102, 'blue-hoodie', 'Blue Hoodie', 'hoodie', '{}', '{}', false, true, '2026-02-01'),
    (3, 1, 103, 'gift-card', 'Gift Card', 'other', '{}', '{}', true, true, '2026-03-01'),
    (4, 1, 104, 'old-tee', 'Old Tee', 'tee', '{}', '{}', false, false, '2026-04-01'),
    (5, 1, 105, 'cargo', 'Cargo Pants', 'pants', '{}', '{}', false, true, '2026-05-01'),
    (6, 2, 201, 'g-tee', 'Genrage Tee', 'tee', '{}', '{}', false, true, '2026-05-01');

INSERT INTO catalog.variants (id, product_id, shopify_variant_id, position, title, size_norm,
                              price, compare_at_price, available) VALUES
    (11, 1, 1001, 1, 'S', 'S', 999.00, 1499.00, true),
    (12, 1, 1002, 2, 'M', 'M', 999.00, NULL, false),
    (13, 1, 1003, 3, 'L', 'L', 1099.00, NULL, true),
    (21, 2, 2001, 1, 'M', 'M', 1999.00, NULL, false),
    (31, 3, 3001, 1, '500', NULL, 500.00, NULL, true),
    (41, 4, 4001, 1, 'M', 'M', 799.00, NULL, true),
    (51, 5, 5001, 1, 'M', 'M', 2499.00, NULL, true),
    (61, 6, 6001, 1, 'M', 'M', 899.00, NULL, true);
"""


@pytest.fixture
def admin():
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS catalog CASCADE")
        conn.execute(CATALOG_SCHEMA_PATH.read_text())
        conn.execute(SEED)
        conn.execute((ROOT / "sql" / "app_schema.sql").read_text())
        if not os.environ.get("TEST_APP_DATABASE_URL"):
            conn.execute("ALTER ROLE app_user LOGIN")  # local trust auth, test DB only
        yield conn


@pytest.fixture
def client(admin):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def ids(page):
    return [item["id"] for item in page["items"]]


# ---------------------------------------------------------------- basics

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_brands_count_hides_gift_cards_and_inactive(client):
    brands = client.get("/brands").json()
    assert [(b["id"], b["product_count"]) for b in brands] == [(1, 3), (2, 1)]


def test_unknown_brand_is_404(client):
    assert client.get("/brands/99/products").status_code == 404


def test_bad_sort_is_422(client):
    assert client.get("/brands/1/products?sort=drop table").status_code == 422


# ---------------------------------------------------------------- listing

def test_default_listing_in_stock_only(client):
    page = client.get("/brands/1/products").json()
    assert ids(page) == [5, 1]  # newest first; sold-out hoodie, gift card, inactive hidden
    assert page["total"] == 2


def test_listing_including_sold_out(client):
    assert ids(client.get("/brands/1/products?in_stock=false").json()) == [5, 2, 1]


def test_card_fields(client):
    card = next(c for c in client.get("/brands/1/products").json()["items"] if c["id"] == 1)
    assert Decimal(card["price"]) == Decimal("999.00")
    assert Decimal(card["compare_at_price"]) == Decimal("1499.00")
    assert Decimal(card["discount_pct"]) == Decimal("33.4")
    assert card["sizes"] == ["S", "L"]  # in-stock sizes, store order
    assert card["url"] == "https://www.fugazee.com/products/black-tee"
    assert card["image_url"] == "https://cdn.shopify.com/1.jpg"
    assert card["in_stock"] is True and card["wishlist_id"] is None


@pytest.mark.parametrize("query, expected", [
    ("size=M", [5]),                 # p1's M is sold out
    ("size=m", [5]),
    ("size=M&in_stock=false", [5, 2, 1]),
    ("on_sale=true", [1]),
    ("category=pants", [5]),
    ("q=cargo", [5]),
    ("q=SUMMER", [1]),               # tags are searched too
    ("q=%25", []),                   # a literal % matches nothing
    ("min_price=1000", [5, 1]),      # p1 has L at 1099
    ("max_price=1000", [1]),
    ("min_price=1000&max_price=1100&size=L", [1]),
    ("sort=price_asc", [1, 5]),
    ("sort=price_desc", [5, 1]),
    ("sort=discount", [1, 5]),
    ("sort=title", [1, 5]),          # Black Tee, Cargo Pants
])
def test_listing_filters(client, query, expected):
    assert ids(client.get(f"/brands/1/products?{query}").json()) == expected


def test_listing_pagination(client):
    page = client.get("/brands/1/products?page=2&page_size=1").json()
    assert ids(page) == [1]
    assert (page["total"], page["page"], page["page_size"]) == (2, 2, 1)


def test_filter_options(client):
    options = client.get("/brands/1/filters").json()
    assert options["sizes"] == ["S", "M", "L"]
    assert options["categories"] == ["hoodie", "pants", "tee"]
    assert Decimal(options["min_price"]) == Decimal("999.00")
    assert Decimal(options["max_price"]) == Decimal("2499.00")


# ---------------------------------------------------------------- detail

def test_product_detail(client):
    product = client.get("/products/1").json()
    assert product["brand_name"] == "Fugazee"
    assert [v["size_norm"] for v in product["variants"]] == ["S", "M", "L"]
    assert [v["available"] for v in product["variants"]] == [True, False, True]
    assert all(v["alert_id"] is None for v in product["variants"])


def test_gift_card_detail_is_404(client):
    assert client.get("/products/3").status_code == 404


# ---------------------------------------------------------------- wishlist

def test_wishlist_flow(client):
    r = client.post("/wishlist", json={"product_id": 1})
    assert r.status_code == 201
    item = r.json()
    assert item["product"]["id"] == 1

    # Adding again returns the same item.
    assert client.post("/wishlist", json={"product_id": 1}).json()["id"] == item["id"]

    page = client.get("/wishlist").json()
    assert ids(page) == [item["id"]] and page["total"] == 1
    assert client.get("/products/1").json()["wishlist_id"] == item["id"]

    assert client.delete(f"/wishlist/{item['id']}").status_code == 204
    assert client.delete(f"/wishlist/{item['id']}").status_code == 404
    assert client.get("/wishlist").json()["total"] == 0


def test_wishlist_rejects_missing_and_gift_card(client):
    assert client.post("/wishlist", json={"product_id": 999}).status_code == 404
    assert client.post("/wishlist", json={"product_id": 3}).status_code == 404


# ---------------------------------------------------------------- alerts

def test_alert_flow(client):
    r = client.post("/alerts", json={"variant_id": 12})  # Black Tee M, sold out
    assert r.status_code == 201
    alert = r.json()
    assert (alert["status"], alert["size_norm"], alert["product_title"]) == ("active", "M", "Black Tee")

    # Only one active alert per variant.
    assert client.post("/alerts", json={"variant_id": 12}).json()["id"] == alert["id"]

    page = client.get("/alerts").json()
    assert ids(page) == [alert["id"]]
    variants = client.get("/products/1").json()["variants"]
    assert [v["alert_id"] for v in variants] == [None, alert["id"], None]

    assert client.delete(f"/alerts/{alert['id']}").status_code == 204
    assert client.delete(f"/alerts/{alert['id']}").status_code == 404
    assert client.get("/alerts").json()["total"] == 0


def test_alert_rejected_when_in_stock(client):
    r = client.post("/alerts", json={"variant_id": 11})
    assert r.status_code == 409


@pytest.mark.parametrize("variant_id", [999, 31, 41])  # missing, gift card, inactive product
def test_alert_rejected_for_hidden_variants(client, variant_id):
    assert client.post("/alerts", json={"variant_id": variant_id}).status_code == 404


def test_alert_checker_query_finds_restocked(client, admin):
    from notifier.check_alerts import RESTOCKED

    alert_id = client.post("/alerts", json={"variant_id": 12}).json()["id"]
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as app_conn:
        assert app_conn.execute(RESTOCKED).fetchall() == []
        admin.execute("UPDATE catalog.variants SET available = true WHERE id = 12")  # restock
        rows = app_conn.execute(RESTOCKED).fetchall()
    assert [r["alert_id"] for r in rows] == [alert_id]
    assert rows[0]["url"] == "https://www.fugazee.com/products/black-tee"


# ---------------------------------------------------------------- security

def test_app_user_cannot_write_catalog(client):
    with psycopg.connect(os.environ["DATABASE_URL"]) as app_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app_conn.execute("UPDATE catalog.variants SET available = true")


def test_cors_allows_only_frontend_origin(client):
    ok = client.get("/brands", headers={"Origin": "https://the-catalogue.vercel.app"})
    assert ok.headers.get("access-control-allow-origin") == "https://the-catalogue.vercel.app"
    other = client.get("/brands", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers
