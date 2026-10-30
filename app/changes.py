"""Read-only change queries shared by listings, recent updates, and wishlists."""

from typing import Literal

ChangeFilter = Literal["all", "new", "price_drop", "restock", "removed"]

# A badge describes an offer that still exists. A stale price drop or restock
# must disappear when that variant sells out, is removed, or changes price again.
RECENT_VALID = """
c.observed_at BETWEEN now() - interval '7 days' AND now()
AND (
    (c.event_type IN ('new', 'relisted') AND p.is_active)
    OR (c.event_type = 'removed' AND NOT p.is_active)
    OR (p.is_active AND v.is_active AND v.available AND (
        c.event_type = 'restock'
        OR (c.event_type = 'price_drop' AND v.price = c.new_price)
    ))
)
"""

RECENT_JOIN = f"""
LEFT JOIN LATERAL (
    SELECT coalesce(jsonb_agg(to_jsonb(recent) ORDER BY recent.observed_at DESC), '[]') AS recent_changes,
           max(recent.observed_at) AS last_changed_at
    FROM (
        SELECT DISTINCT ON (c.event_type)
               c.id, c.event_type, c.variant_id, c.variant_label,
               c.old_price::text AS old_price, c.new_price::text AS new_price, c.observed_at
        FROM catalog.product_changes c
        LEFT JOIN catalog.variants v ON v.id = c.variant_id
        WHERE c.product_id = p.id AND {RECENT_VALID}
        ORDER BY c.event_type, c.observed_at DESC, c.id DESC
    ) recent
) updates ON true
"""


def change_filter(kind, params, size=None):
    """One matching event; a size filter must match the size that actually changed."""
    conditions = ["c.product_id = p.id", RECENT_VALID]
    if kind != "all":
        conditions.append("c.event_type = %(change)s")
        params["change"] = kind
    if size and kind in ("price_drop", "restock"):
        conditions.append("v.size_norm = %(change_size)s")
        params["change_size"] = size.strip().upper()
    return f"""EXISTS (SELECT 1 FROM catalog.product_changes c
                       LEFT JOIN catalog.variants v ON v.id = c.variant_id
                       WHERE {' AND '.join(conditions)})"""


def history(conn, product_id):
    return conn.execute(
        """SELECT id, event_type, variant_id, variant_label, old_price, new_price, observed_at
           FROM catalog.product_changes WHERE product_id = %s
           ORDER BY observed_at DESC, id DESC LIMIT 50""",
        (product_id,),
    ).fetchall()
