from fastapi import APIRouter, Depends

from app.db import get_conn
from app.schemas import Brand

router = APIRouter(tags=["brands"])


@router.get("/brands", response_model=list[Brand])
def list_brands(conn=Depends(get_conn)):
    """All active brands, for the tabs. Only 10 rows, so no paging."""
    return conn.execute(
        """
        SELECT b.id, b.name, b.base_url,
               count(p.id) FILTER (WHERE p.is_active AND NOT p.is_gift_card) AS product_count
        FROM catalog.brands b
        LEFT JOIN catalog.products p ON p.brand_id = b.id
        WHERE b.is_active
        GROUP BY b.id
        ORDER BY b.id
        """
    ).fetchall()
