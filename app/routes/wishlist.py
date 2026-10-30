import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.db import get_conn
from app.changes import ChangeFilter, change_filter
from app.queries import CARD_SELECT, card_from_row
from app.schemas import Page, WishlistCreate, WishlistItem

router = APIRouter(tags=["wishlist"])
log = logging.getLogger("app.wishlist")


def _item(row: dict) -> dict:
    return {"id": row["wishlist_id"], "added_at": row["wishlist_added_at"], "product": card_from_row(row)}


@router.get("/wishlist", response_model=Page[WishlistItem])
def list_wishlist(
    change: ChangeFilter | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    conn=Depends(get_conn),
):
    params = {"limit": page_size, "offset": (page - 1) * page_size}
    where = ["w.id IS NOT NULL"]
    if change == "removed":
        where.append("NOT p.is_active")  # Saved removed products remain visible after seven days.
    elif change:
        where.append(change_filter(change, params))
    where_sql = " AND ".join(where)
    total = conn.execute(
        "SELECT count(*) AS n FROM catalog.products p "
        f"JOIN app.wishlist w ON w.product_id = p.id WHERE {where_sql}", params,
    ).fetchone()["n"]
    rows = conn.execute(
        f"{CARD_SELECT} WHERE {where_sql} ORDER BY w.added_at DESC, w.id DESC "
        "LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
    return {"items": [_item(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/wishlist", response_model=WishlistItem, status_code=status.HTTP_201_CREATED)
def add_to_wishlist(body: WishlistCreate, conn=Depends(get_conn)):
    """Add a product. Adding it a second time returns the existing item."""
    inserted = conn.execute(
        """
        INSERT INTO app.wishlist (product_id)
        SELECT id FROM catalog.products WHERE id = %s AND NOT is_gift_card
        ON CONFLICT (product_id) DO NOTHING
        RETURNING id
        """,
        (body.product_id,),
    ).fetchone()
    row = conn.execute(f"{CARD_SELECT} WHERE p.id = %s AND w.id IS NOT NULL", (body.product_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Product not found")
    log.info(
        "%s product %s (%s / %s) as item %s",
        "added" if inserted else "already had", body.product_id, row["brand_name"], row["title"], row["wishlist_id"],
    )
    return _item(row)


@router.delete("/wishlist/{wishlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_wishlist(wishlist_id: int, conn=Depends(get_conn)):
    deleted = conn.execute("DELETE FROM app.wishlist WHERE id = %s RETURNING id", (wishlist_id,)).fetchone()
    if not deleted:
        raise HTTPException(404, "Wishlist item not found")
    log.info("removed item %s", wishlist_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
