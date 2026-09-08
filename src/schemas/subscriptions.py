from datetime import datetime
from decimal import Decimal
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
    # Deprecated input retained for API compatibility. The service always uses
    # MP_SUBSCRIPTION_WEBHOOK_URL from trusted configuration.
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
    next_payment_at: datetime | None = None
    created_at: datetime

    class Config:
        orm_mode = True


class BillingCycleCreate(BaseModel):
    period_start: datetime
    period_end: datetime
    active_seats: int
    unit_amount: Decimal
    minimum_amount: Decimal = Decimal("0")


class BillingCycleResponse(BaseModel):
    id: int
    subscription_id: int
    period_start: datetime
    period_end: datetime
    active_seats: int
    unit_amount: Decimal
    minimum_amount: Decimal
    amount: Decimal
    currency: str
    status: str
    gateway_authorized_payment_id: str | None
    gateway_payment_id: str | None
    created_at: datetime

    class Config:
        orm_mode = True


class EntitlementResponse(BaseModel):
    user_id: str
    active: bool
    subscription_id: int | None = None
    plan_id: int | None = None
    access_until: datetime | None = None
