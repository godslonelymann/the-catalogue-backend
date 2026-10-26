from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import get_conn
from app.queries import CARD_SELECT, card_from_row, like_pattern, size_sort_key
from app.schemas import FilterOptions, Page, ProductCard, ProductDetail

router = APIRouter(tags=["products"])

# Whitelisted ORDER BY clauses. User input never goes into SQL text.
SORTS = {
    "newest": "p.shopify_created_at DESC NULLS LAST, p.id DESC",
    "price_asc": "s.price ASC NULLS LAST, p.id",
    "price_desc": "s.price DESC NULLS LAST, p.id",
    "discount": "s.discount_pct DESC NULLS LAST, p.id",
    "title": "p.title, p.id",
}

# Products shown in listings: active, not gift cards.
VISIBLE = "p.is_active AND NOT p.is_gift_card"


def _brand_exists(conn, brand_id: int) -> None:
    if not conn.execute("SELECT 1 FROM catalog.brands WHERE id = %s", (brand_id,)).fetchone():
        raise HTTPException(404, "Brand not found")


@router.get("/brands/{brand_id}/products", response_model=Page[ProductCard])
def list_products(
    brand_id: int,
    size: str | None = Query(None, max_length=40, description="size_norm, e.g. M, XL, 32"),
    category: str | None = Query(None, max_length=40),
    min_price: Decimal | None = Query(None, ge=0),
    max_price: Decimal | None = Query(None, ge=0),
    in_stock: bool = Query(True, description="Default true, else Urban Monkey is mostly sold out"),
    on_sale: bool = False,
    q: str | None = Query(None, max_length=100),
    sort: Literal["newest", "price_asc", "price_desc", "discount", "title"] = "newest",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    conn=Depends(get_conn),
):
    _brand_exists(conn, brand_id)

    where = ["p.brand_id = %(brand_id)s", VISIBLE]
    params: dict = {"brand_id": brand_id}
    if category:
        where.append("p.category = %(category)s")
        params["category"] = category.lower()
    if q and q.strip():
        where.append("(p.title ILIKE %(q)s OR array_to_string(p.tags, ' ') ILIKE %(q)s)")
        params["q"] = like_pattern(q.strip())

    # Variant filters: one variant must match all of them at once
    # (e.g. size M that is in stock and on sale).
    variant_where = ["v.product_id = p.id", "v.is_active"]
    if in_stock:
        variant_where.append("v.available")
    if on_sale:
        variant_where.append("v.compare_at_price IS NOT NULL")
    if size:
        variant_where.append("v.size_norm = %(size)s")
        params["size"] = size.strip().upper()
    if min_price is not None:
        variant_where.append("v.price >= %(min_price)s")
        params["min_price"] = min_price
    if max_price is not None:
        variant_where.append("v.price <= %(max_price)s")
        params["max_price"] = max_price
    where.append(f"EXISTS (SELECT 1 FROM catalog.variants v WHERE {' AND '.join(variant_where)})")

    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT count(*) AS n FROM catalog.products p WHERE {where_sql}", params
    ).fetchone()["n"]

    params.update(limit=page_size, offset=(page - 1) * page_size)
    rows = conn.execute(
        f"{CARD_SELECT} WHERE {where_sql} ORDER BY {SORTS[sort]} LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
    return {"items": [card_from_row(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/brands/{brand_id}/filters", response_model=FilterOptions)
def filter_options(brand_id: int, conn=Depends(get_conn)):
    """Values for the filter controls of one brand tab."""
    _brand_exists(conn, brand_id)
    sizes = [
        r["size_norm"]
        for r in conn.execute(
            f"""
            SELECT DISTINCT v.size_norm
            FROM catalog.variants v JOIN catalog.products p ON p.id = v.product_id
            WHERE p.brand_id = %s AND {VISIBLE} AND v.is_active AND v.size_norm IS NOT NULL
            """,
            (brand_id,),
        ).fetchall()
    ]
    categories = [
        r["category"]
        for r in conn.execute(
            f"""
            SELECT p.category, count(*) AS n FROM catalog.products p
            WHERE p.brand_id = %s AND {VISIBLE}
            GROUP BY p.category ORDER BY n DESC, p.category
            """,
            (brand_id,),
        ).fetchall()
    ]
    prices = conn.execute(
        f"""
        SELECT min(v.price) AS min_price, max(v.price) AS max_price
        FROM catalog.variants v JOIN catalog.products p ON p.id = v.product_id
        WHERE p.brand_id = %s AND {VISIBLE} AND v.is_active
        """,
        (brand_id,),
    ).fetchone()
    # Only letter sizes, numbers and ONE SIZE: values like "BLK_001" or "52MM" are not useful filters.
    sizes = sorted((s for s in sizes if size_sort_key(s)[0] < 3), key=size_sort_key)
    return {"sizes": sizes, "categories": categories, **prices}


@router.get("/products/{product_id}", response_model=ProductDetail)
def product_detail(product_id: int, conn=Depends(get_conn)):
    """One product with its variants in store order. Inactive products are still returned
    (a wishlist may point at them); the frontend shows them as no longer listed."""
    product = conn.execute(
        """
        SELECT p.id, p.brand_id, b.name AS brand_name, p.title, p.category, p.product_type,
               p.tags, p.body_html, p.image_urls, p.options, p.is_active, p.shopify_created_at,
               b.base_url || '/products/' || p.handle AS url,
               w.id AS wishlist_id
        FROM catalog.products p
        JOIN catalog.brands b ON b.id = p.brand_id
        LEFT JOIN app.wishlist w ON w.product_id = p.id
        WHERE p.id = %s AND NOT p.is_gift_card
        """,
        (product_id,),
    ).fetchone()
    if not product:
        raise HTTPException(404, "Product not found")

    product["variants"] = conn.execute(
        """
        SELECT v.id, v.position, v.title, v.option1, v.option2, v.option3, v.size_raw, v.size_norm,
               v.price, v.compare_at_price, v.discount_pct, v.available, a.id AS alert_id
        FROM catalog.variants v
        LEFT JOIN app.alerts a ON a.variant_id = v.id AND a.status = 'active'
        WHERE v.product_id = %s AND v.is_active
        ORDER BY v.position NULLS LAST, v.id
        """,
        (product_id,),
    ).fetchall()
    return product
