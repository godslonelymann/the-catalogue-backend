"""Pydantic models for request bodies and responses.

Money fields are Decimal. They are sent as JSON strings (e.g. "1299.00") so no precision is lost.
"""

from datetime import datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Brand(BaseModel):
    id: int
    name: str
    base_url: str
    product_count: int


class FilterOptions(BaseModel):
    sizes: list[str]
    categories: list[str]
    min_price: Decimal | None
    max_price: Decimal | None
    last_checked_at: datetime | None = None


class ProductChange(BaseModel):
    id: int
    event_type: Literal["new", "price_drop", "price_increase", "restock", "sold_out", "removed", "relisted"]
    variant_id: int | None
    variant_label: str | None
    old_price: Decimal | None
    new_price: Decimal | None
    observed_at: datetime


class ProductCard(BaseModel):
    id: int
    brand_id: int
    brand_name: str
    title: str
    category: str
    image_url: str | None
    url: str
    price: Decimal | None
    compare_at_price: Decimal | None
    discount_pct: Decimal | None
    in_stock: bool
    sizes: list[str]          # sizes in stock, in store order
    is_active: bool
    shopify_created_at: datetime | None
    wishlist_id: int | None
    last_seen_at: datetime
    recent_changes: list[ProductChange] = Field(default_factory=list)


class UpdatePage(Page[ProductCard]):
    last_checked_at: datetime | None


class Variant(BaseModel):
    id: int
    position: int | None
    title: str | None
    option1: str | None
    option2: str | None
    option3: str | None
    size_raw: str | None
    size_norm: str | None
    price: Decimal
    compare_at_price: Decimal | None
    discount_pct: Decimal | None
    available: bool
    alert_id: int | None      # id of the active restock alert on this variant, if any


class ProductDetail(BaseModel):
    id: int
    brand_id: int
    brand_name: str
    title: str
    category: str
    product_type: str | None
    tags: list[str]
    body_html: str | None     # raw store HTML: the frontend must sanitize it
    image_urls: list[str]
    options: list[dict]
    url: str
    is_active: bool
    shopify_created_at: datetime | None
    wishlist_id: int | None
    variants: list[Variant]   # sorted by store position
    last_seen_at: datetime
    changes: list[ProductChange] = Field(default_factory=list)


class WishlistCreate(BaseModel):
    product_id: int


class WishlistItem(BaseModel):
    id: int
    added_at: datetime
    product: ProductCard


class AlertCreate(BaseModel):
    variant_id: int


class Alert(BaseModel):
    id: int
    status: str
    created_at: datetime
    last_fired_at: datetime | None
    variant_id: int
    variant_title: str | None
    size_norm: str | None
    price: Decimal
    compare_at_price: Decimal | None
    discount_pct: Decimal | None
    available: bool
    product_id: int
    product_title: str
    brand_name: str
    image_url: str | None
    url: str
