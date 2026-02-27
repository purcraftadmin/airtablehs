"""
Pydantic schemas for request/response validation.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Webhook payloads ─────────────────────────────────────────────────────────

class LineItem(BaseModel):
    line_item_id: str
    sku: str
    qty: int = Field(..., gt=0)


class OrderWebhookPayload(BaseModel):
    site_id: str
    order_id: str
    status: str
    line_items: List[LineItem]


class RefundCancelPayload(BaseModel):
    site_id: str
    order_id: str
    line_items: List[LineItem]
    event_type: str = "refund"   # 'refund' | 'cancel'


# ── Admin / query responses ───────────────────────────────────────────────────

class StockRow(BaseModel):
    sku: str
    on_hand: int
    reserved: int
    updated_at: str

    model_config = {"from_attributes": True}


class MappingRefreshResult(BaseModel):
    site_id: str
    inserted: int
    updated: int
    errors: List[str] = []


class HealthResponse(BaseModel):
    status: str = "ok"
    db: str = "ok"
