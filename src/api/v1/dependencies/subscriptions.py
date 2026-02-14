from typing import Generator

from sqlalchemy.orm import Session

from config.settings import settings
from db.session import SessionLocal
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_mp_subscription_service() -> MercadopagoSubscriptionService:
    return MercadopagoSubscriptionService(access_token=settings.mercadopago_access_token)


def get_mp_payment_service() -> MercadopagoPaymentService:
    return MercadopagoPaymentService(access_token=settings.mercadopago_access_token)
