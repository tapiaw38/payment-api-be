from datetime import datetime
from pydantic import BaseModel


class PaymentMethodCreate(BaseModel):
    card_token_id: str
    last_four_digits: str
    payment_method_id: str
    cardholder_name: str
    expiration_month: str
    expiration_year: str
    is_default: bool = False
    payer_email: str | None = None
    # Raw card fields used only for server-side tokenization; never persisted to DB.
    card_number: str | None = None
    security_code: str | None = None
    doc_type: str | None = None
    doc_number: str | None = None


class PaymentMethodResponse(BaseModel):
    id: int
    user_id: str
    gateway: str
    last_four_digits: str
    payment_method_id: str
    cardholder_name: str
    expiration_month: str
    expiration_year: str
    is_default: bool
    mp_customer_id: str | None = None
    mp_card_id: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class PaymentMethodUpdate(BaseModel):
    is_default: bool | None = None
