from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.db import get_conn
from app.queries import CARD_SELECT, card_from_row
from app.schemas import Page, WishlistCreate, WishlistItem

router = APIRouter(tags=["wishlist"])


def _item(row: dict) -> dict:
    return {"id": row["wishlist_id"], "added_at": row["wishlist_added_at"], "product": card_from_row(row)}


@router.get("/wishlist", response_model=Page[WishlistItem])
def list_wishlist(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    conn=Depends(get_conn),
):
    total = conn.execute("SELECT count(*) AS n FROM app.wishlist").fetchone()["n"]
    rows = conn.execute(
        f"{CARD_SELECT} WHERE w.id IS NOT NULL ORDER BY w.added_at DESC, w.id DESC "
        "LIMIT %(limit)s OFFSET %(offset)s",
        {"limit": page_size, "offset": (page - 1) * page_size},
    ).fetchall()
    return {"items": [_item(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/wishlist", response_model=WishlistItem, status_code=status.HTTP_201_CREATED)
def add_to_wishlist(body: WishlistCreate, conn=Depends(get_conn)):
    """Add a product. Adding it a second time returns the existing item."""
    conn.execute(
        """
        INSERT INTO app.wishlist (product_id)
        SELECT id FROM catalog.products WHERE id = %s AND NOT is_gift_card
        ON CONFLICT (product_id) DO NOTHING
        """,
        (body.product_id,),
    )
    row = conn.execute(f"{CARD_SELECT} WHERE p.id = %s AND w.id IS NOT NULL", (body.product_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Product not found")
    return _item(row)


@router.delete("/wishlist/{wishlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_wishlist(wishlist_id: int, conn=Depends(get_conn)):
    deleted = conn.execute("DELETE FROM app.wishlist WHERE id = %s RETURNING id", (wishlist_id,)).fetchone()
    if not deleted:
        raise HTTPException(404, "Wishlist item not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
