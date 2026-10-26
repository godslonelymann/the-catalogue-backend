"""SQL shared by several routes, plus small helpers."""

import re

# One product card. The shown price is the cheapest in-stock variant
# (or the cheapest variant when all are sold out); compare_at_price and
# discount_pct come from that same variant.
CARD_SELECT = """
SELECT p.id, p.brand_id, b.name AS brand_name, p.title, p.category,
       p.image_urls[1] AS image_url,
       b.base_url || '/products/' || p.handle AS url,
       p.is_active, p.shopify_created_at,
       s.price, s.compare_at_price, s.discount_pct,
       coalesce(s.in_stock, false) AS in_stock,
       coalesce(s.sizes, '{}') AS sizes,
       w.id AS wishlist_id, w.added_at AS wishlist_added_at
FROM catalog.products p
JOIN catalog.brands b ON b.id = p.brand_id
LEFT JOIN app.wishlist w ON w.product_id = p.id
LEFT JOIN LATERAL (
    SELECT (array_agg(v.price            ORDER BY v.available DESC, v.price, v.position))[1] AS price,
           (array_agg(v.compare_at_price ORDER BY v.available DESC, v.price, v.position))[1] AS compare_at_price,
           (array_agg(v.discount_pct     ORDER BY v.available DESC, v.price, v.position))[1] AS discount_pct,
           bool_or(v.available) AS in_stock,
           array_agg(v.size_norm ORDER BY v.position)
               FILTER (WHERE v.available AND v.size_norm IS NOT NULL) AS sizes
    FROM catalog.variants v
    WHERE v.product_id = p.id AND v.is_active
) s ON true
"""

ALERT_SELECT = """
SELECT a.id, a.status, a.created_at, a.last_fired_at,
       v.id AS variant_id, v.title AS variant_title, v.size_norm,
       v.price, v.compare_at_price, v.discount_pct, v.available,
       p.id AS product_id, p.title AS product_title, b.name AS brand_name,
       p.image_urls[1] AS image_url,
       b.base_url || '/products/' || p.handle AS url
FROM app.alerts a
JOIN catalog.variants v ON v.id = a.variant_id
JOIN catalog.products p ON p.id = v.product_id
JOIN catalog.brands b ON b.id = p.brand_id
"""

LETTER_SIZES = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "4XL", "5XL"]
_NUMBER = re.compile(r"\d+(\.\d+)?")


def size_sort_key(size: str) -> tuple:
    """Letter sizes in clothing order, then numbers, then ONE SIZE, then anything else."""
    if size in LETTER_SIZES:
        return (0, LETTER_SIZES.index(size), "")
    if _NUMBER.fullmatch(size):
        return (1, float(size), "")
    if size == "ONE SIZE":
        return (2, 0, "")
    return (3, 0, size)


def unique(values: list) -> list:
    """Remove duplicates, keep the first position (e.g. "M" for M/Black and M/White)."""
    return list(dict.fromkeys(values))


def like_pattern(text: str) -> str:
    """Search text -> ILIKE pattern. % and _ typed by the user are matched literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def card_from_row(row: dict) -> dict:
    card = dict(row)
    card["sizes"] = unique(card["sizes"])
    return card
