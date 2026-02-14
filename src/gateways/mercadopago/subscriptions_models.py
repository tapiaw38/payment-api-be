from typing import Any

from pydantic import BaseModel


class MPPlanCreate(BaseModel):
    reason: str
    auto_recurring: dict[str, Any]
    payment_methods_allowed: list[dict[str, Any]] | None = None


class MPSubscriptionCreate(BaseModel):
    preapproval_plan_id: str
    reason: str
    external_reference: str | None = None
    payer_email: str
    card_token_id: str
    status: str = "authorized"
    back_url: str | None = None
    notification_url: str | None = None


class MPSubscriptionResponse(BaseModel):
    id: str
    status: str
    init_point: str | None = None
    date_created: str | None = None
    date_approved: str | None = None
    last_modified: str | None = None
    reason: str | None = None
    external_reference: str | None = None
    payer_id: str | None = None
    payer_email: str | None = None
    preapproval_plan_id: str | None = None
    payment_method_id: str | None = None
    next_payment_date: str | None = None
    summarized: dict[str, Any] | None = None
