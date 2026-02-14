from typing import Any

from pydantic import BaseModel


class PaymentPayer(BaseModel):
    email: str
    id: str | None = None
    type: str | None = None


class PaymentCreate(BaseModel):
    transaction_amount: float
    token: str
    payment_method_id: str
    payer: PaymentPayer
    installments: int = 1
    description: str | None = None
    external_reference: str | None = None
    collector_id: str | None = None


class PaymentResponse(BaseModel):
    id: int
    status: str
    status_detail: str | None = None
    transaction_amount: float | None = None
    date_approved: str | None = None
    date_created: str | None = None
    date_last_updated: str | None = None
    external_reference: str | None = None

    class Config:
        extra = "allow"
