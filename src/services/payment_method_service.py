import logging

from sqlalchemy.orm import Session

from db.models import PaymentMethod
from gateways.mercadopago.models import TokenDataInput

logger = logging.getLogger(__name__)


class PaymentMethodService:
    def __init__(self, db: Session):
        self.db = db

    def create_payment_method(self, user_id: str, data, mp_payment_service=None) -> PaymentMethod:
        if data.is_default:
            self._unset_default_for_user(user_id)

        mp_customer_id = None
        mp_card_id = None

        if mp_payment_service and data.payer_email:
            try:
                token_for_customer = data.card_token_id
                if data.card_number:
                    token_data = TokenDataInput(
                        card_number=data.card_number,
                        security_code=data.security_code,
                        card_expiration_month=data.expiration_month,
                        card_expiration_year=data.expiration_year,
                        cardholder_name=data.cardholder_name,
                        doc_type=data.doc_type,
                        doc_number=data.doc_number,
                    )
                    token_for_customer = mp_payment_service.create_card_token(token_data)
                    logger.info("Created server-side card token for customer card saving")
                mp_customer_id = mp_payment_service.get_or_create_customer(data.payer_email)
                logger.info("MP customer_id: %s, saving token: %s", mp_customer_id, token_for_customer)
                mp_card_id = mp_payment_service.save_card_to_customer(mp_customer_id, token_for_customer)
                logger.info("MP card saved: %s", mp_card_id)
            except Exception as e:
                # Log but don't fail - fall back to token-based approach
                import traceback
                logger.warning("Failed to create MP customer card: %s\n%s", e, traceback.format_exc())
                mp_customer_id = None
                mp_card_id = None

        payment_method = PaymentMethod(
            user_id=user_id,
            gateway="mercadopago",
            card_token_id=data.card_token_id,
            last_four_digits=data.last_four_digits,
            payment_method_id=data.payment_method_id,
            cardholder_name=data.cardholder_name,
            expiration_month=data.expiration_month,
            expiration_year=data.expiration_year,
            is_default=1 if data.is_default else 0,
            mp_customer_id=mp_customer_id,
            mp_card_id=mp_card_id,
        )
        self.db.add(payment_method)
        self.db.commit()
        self.db.refresh(payment_method)
        return payment_method

    def get_payment_methods_by_user(self, user_id: str) -> list[PaymentMethod]:
        return (
            self.db.query(PaymentMethod)
            .filter(PaymentMethod.user_id == user_id)
            .order_by(PaymentMethod.is_default.desc(), PaymentMethod.created_at.desc())
            .all()
        )

    def get_default_payment_method(self, user_id: str) -> PaymentMethod | None:
        return (
            self.db.query(PaymentMethod)
            .filter(PaymentMethod.user_id == user_id, PaymentMethod.is_default == 1)
            .first()
        )

    def get_payment_method(self, payment_method_id: int, user_id: str) -> PaymentMethod | None:
        return (
            self.db.query(PaymentMethod)
            .filter(PaymentMethod.id == payment_method_id, PaymentMethod.user_id == user_id)
            .first()
        )

    def update_payment_method(self, payment_method_id: int, user_id: str, data) -> PaymentMethod | None:
        payment_method = self.get_payment_method(payment_method_id, user_id)
        if not payment_method:
            return None

        if data.is_default is not None:
            if data.is_default:
                self._unset_default_for_user(user_id)
            payment_method.is_default = 1 if data.is_default else 0

        self.db.commit()
        self.db.refresh(payment_method)
        return payment_method

    def delete_payment_method(self, payment_method_id: int, user_id: str) -> bool:
        payment_method = self.get_payment_method(payment_method_id, user_id)
        if not payment_method:
            return False

        self.db.delete(payment_method)
        self.db.commit()
        return True

    def _unset_default_for_user(self, user_id: str):
        self.db.query(PaymentMethod).filter(
            PaymentMethod.user_id == user_id,
            PaymentMethod.is_default == 1
        ).update({"is_default": 0})
        self.db.flush()
