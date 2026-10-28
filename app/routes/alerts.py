import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.db import get_conn
from app.queries import ALERT_SELECT
from app.schemas import Alert, AlertCreate, Page

router = APIRouter(tags=["alerts"])
log = logging.getLogger("app.alerts")


@router.get("/alerts", response_model=Page[Alert])
def list_alerts(
    status_: Literal["active", "fired"] | None = Query(None, alias="status",
                                                       description="Default: active and fired"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    conn=Depends(get_conn),
):
    statuses = [status_] if status_ else ["active", "fired"]
    params = {"statuses": statuses, "limit": page_size, "offset": (page - 1) * page_size}
    total = conn.execute(
        "SELECT count(*) AS n FROM app.alerts WHERE status = ANY(%(statuses)s)", params
    ).fetchone()["n"]
    rows = conn.execute(
        f"{ALERT_SELECT} WHERE a.status = ANY(%(statuses)s) "
        "ORDER BY a.status, a.created_at DESC, a.id DESC LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.post("/alerts", response_model=Alert, status_code=status.HTTP_201_CREATED)
def create_alert(body: AlertCreate, conn=Depends(get_conn)):
    """Watch a sold-out variant. Rejected with 409 when the variant is in stock now.
    Creating it a second time returns the existing active alert."""
    variant = conn.execute(
        """
        SELECT v.available
        FROM catalog.variants v JOIN catalog.products p ON p.id = v.product_id
        WHERE v.id = %s AND v.is_active AND p.is_active AND NOT p.is_gift_card
        """,
        (body.variant_id,),
    ).fetchone()
    if not variant:
        raise HTTPException(404, "Variant not found")
    if variant["available"]:
        raise HTTPException(409, "This size is in stock now. Alerts are only for sold-out sizes.")

    inserted = conn.execute(
        """
        INSERT INTO app.alerts (variant_id) VALUES (%s)
        ON CONFLICT (variant_id) WHERE status = 'active' DO NOTHING
        RETURNING id
        """,
        (body.variant_id,),
    ).fetchone()
    alert = conn.execute(
        f"{ALERT_SELECT} WHERE a.variant_id = %s AND a.status = 'active'", (body.variant_id,)
    ).fetchone()
    log.info(
        "%s alert %s on variant %s (%s / %s, size %s)",
        "created" if inserted else "already had", alert["id"], body.variant_id,
        alert["brand_name"], alert["product_title"], alert["size_norm"] or alert["variant_title"],
    )
    return alert


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_alert(alert_id: int, conn=Depends(get_conn)):
    """Cancel an alert (rows are kept, status becomes 'cancelled')."""
    cancelled = conn.execute(
        "UPDATE app.alerts SET status = 'cancelled' WHERE id = %s AND status <> 'cancelled' RETURNING id",
        (alert_id,),
    ).fetchone()
    if not cancelled:
        raise HTTPException(404, "Alert not found")
    log.info("cancelled alert %s", alert_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
