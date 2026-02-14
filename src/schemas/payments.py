from datetime import datetime

from pydantic import BaseModel


class PaymentPayer(BaseModel):
    email: str


class PaymentCreate(BaseModel):
    transaction_amount: float
    token: str
    payment_method_id: str
    payer: PaymentPayer
    installments: int = 1
    description: str | None = None
    external_reference: str | None = None
    user_id: str | None = None


class PaymentWithSavedMethodCreate(BaseModel):
    transaction_amount: float
    payment_method_id: int
    payer: PaymentPayer
    installments: int = 1
    description: str | None = None
    external_reference: str | None = None
    user_id: str | None = None
    collector_id: str | None = None
    security_code: str | None = None


class PaymentResponse(BaseModel):
    id: int
    gateway_payment_id: str | None
    amount: float
    currency: str
    status: str
    user_id: str | None
    external_reference: str | None
    description: str | None
    created_at: datetime

    class Config:
        orm_mode = True
