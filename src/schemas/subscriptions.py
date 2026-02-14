from datetime import datetime
from typing import Any

from pydantic import BaseModel


class PlanCreate(BaseModel):
    name: str
    description: str | None = None
    amount: float
    currency: str = "ARS"
    interval: str = "month"
    interval_count: int = 1


class PlanResponse(BaseModel):
    id: int
    name: str
    description: str | None
    amount: float
    currency: str
    interval: str
    interval_count: int
    gateway_plan_id: str | None
    active: int
    created_at: datetime

    class Config:
        orm_mode = True


class SubscriptionCreate(BaseModel):
    plan_id: int
    user_id: str
    payer_email: str
    card_token_id: str
    notification_url: str | None = None


class SubscriptionResponse(BaseModel):
    id: int
    plan_id: int
    user_id: str
    gateway_subscription_id: str | None
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: int
    created_at: datetime

    class Config:
        orm_mode = True
