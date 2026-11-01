import logging
from sqlalchemy.orm import Session

from db.models import Payment, PaymentMethod
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.payment_models import PaymentCreate as GatewayPaymentCreate, PaymentPayer
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from schemas.payments import PaymentCreate, PaymentWithSavedMethodCreate

logger = logging.getLogger(__name__)

SETTLED_STATUSES = ("approved", "rejected", "cancelled", "refunded", "charged_back")


class PaymentService:
    def __init__(
        self,
        db: Session,
        mp_payment: MercadopagoPaymentService,
        tenant: str | None = None,
    ):
        self.db = db
        self.mp = mp_payment
        self.tenant = tenant

    def _scoped(self, query):
        if self.tenant is None:
            return query
        return query.filter(Payment.tenant == self.tenant)

    def _already_charged(self, external_reference: str | None) -> Payment | None:
        if not external_reference:
            return None
        return self._scoped(
            self.db.query(Payment).filter(Payment.external_reference == external_reference)
        ).first()

    def _idempotency_key(self, external_reference: str) -> str:
        return f"payment:{self.tenant or 'default'}:{external_reference}"

    def create_payment(self, data: PaymentCreate) -> Payment:
        charged = self._already_charged(data.external_reference)
        if charged:
            return charged
        payment = Payment(
            tenant=self.tenant or "default",
            gateway="mercadopago",
            amount=data.transaction_amount,
            currency="ARS",
            status="pending",
            user_id=data.user_id,
            external_reference=data.external_reference,
            description=data.description,
        )
        self.db.add(payment)
        self.db.flush()
        reference = data.external_reference or str(payment.id)
        try:
            gateway_data = GatewayPaymentCreate(
                transaction_amount=data.transaction_amount,
                token=data.token,
                payment_method_id=data.payment_method_id,
                payer=data.payer,
                installments=data.installments,
                description=data.description,
                external_reference=reference,
            )
            result = self.mp.create_payment(
                gateway_data, idempotency_key=self._idempotency_key(reference)
            )
            payment.gateway_payment_id = str(result.get("id", ""))
            payment.status = result.get("status", "pending")
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def create_payment_with_saved_method(self, data: PaymentWithSavedMethodCreate) -> Payment:
        charged = self._already_charged(data.external_reference)
        if charged:
            return charged

        query = self.db.query(PaymentMethod).filter(
            PaymentMethod.id == data.payment_method_id,
            PaymentMethod.user_id == data.user_id,
            PaymentMethod.is_default == 1,
        )
        if self.tenant is not None:
            query = query.filter(PaymentMethod.tenant == self.tenant)
        payment_method = query.first()

        if not payment_method:
            raise ValueError("Payment method not found or not default")

        payer_email = data.payer.email
        if payment_method.mp_customer_id:
            customer_email = self.mp.get_customer_email(payment_method.mp_customer_id)
            if customer_email:
                payer_email = customer_email

        # Use Customer Cards API for a fresh token when available; fall back to the stored token
        if payment_method.mp_customer_id and payment_method.mp_card_id:
            fresh_token = self.mp.create_card_token_from_saved(
                payment_method.mp_customer_id,
                payment_method.mp_card_id,
                data.security_code,
            )
        else:
            fresh_token = payment_method.card_token_id

        payment = Payment(
            tenant=self.tenant or "default",
            gateway="mercadopago",
            amount=data.transaction_amount,
            currency="ARS",
            status="pending",
            user_id=data.user_id,
            external_reference=data.external_reference,
            description=data.description,
        )
        self.db.add(payment)
        self.db.flush()
        reference = data.external_reference or str(payment.id)
        try:
            # When using a saved customer card token, MP requires payer.type/id
            # but must NOT receive payment_method_id (it's inferred from the token).
            payer_info = PaymentPayer(
                email=payer_email,
                type="customer",
                id=payment_method.mp_customer_id,
            )

            gateway_data = GatewayPaymentCreate(
                transaction_amount=data.transaction_amount,
                token=fresh_token,
                payment_method_id=payment_method.payment_method_id,
                payer=payer_info,
                installments=data.installments,
                description=data.description,
                external_reference=reference,
                collector_id=data.collector_id,
            )
            result = self.mp.create_payment(
                gateway_data, idempotency_key=self._idempotency_key(reference)
            )
            payment.gateway_payment_id = str(result.get("id", ""))
            payment.status = result.get("status", "pending")
        except MercadopagoAPIException as e:
            logger.warning(
                "Mercado Pago payment rejected payment=%s status=%s code=%s",
                payment.id,
                e.status_code,
                e.error_code or "unknown",
            )
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def create_preference(self, data) -> dict:
        items = [
            {
                "title": item.title,
                "quantity": item.quantity,
                "unit_price": round(item.unit_price, 2),
                "currency_id": item.currency_id,
            }
            for item in data.items
        ]
        back_urls = {
            "success": data.back_urls.success,
            "failure": data.back_urls.failure,
            "pending": data.back_urls.pending,
        }
        result = self.mp.create_preference(
            items=items,
            payer_email=data.payer_email,
            external_reference=data.external_reference,
            back_urls=back_urls,
            notification_url=data.notification_url,
        )
        return {
            "preference_id": result.get("id", ""),
            "init_point": result.get("init_point", ""),
            "sandbox_init_point": result.get("sandbox_init_point", ""),
        }

    def get_payment(self, payment_id: int, user_id: str | None = None) -> Payment | None:
        query = self._scoped(self.db.query(Payment).filter(Payment.id == payment_id))
        if user_id is not None:
            query = query.filter(Payment.user_id == user_id)
        return query.first()

    def get_payment_by_gateway_id(self, gateway_payment_id: str) -> Payment | None:
        return self._scoped(
            self.db.query(Payment).filter(Payment.gateway_payment_id == gateway_payment_id)
        ).first()

    def update_payment_status(self, gateway_payment_id: str, status: str) -> Payment | None:
        payment = self.get_payment_by_gateway_id(gateway_payment_id)
        if not payment:
            return None
        if not status:
            return payment
        if payment.status in SETTLED_STATUSES and status not in SETTLED_STATUSES:
            logger.info(
                "Ignoring stale payment status payment=%s current=%s incoming=%s",
                payment.id,
                payment.status,
                status,
            )
            return payment
        payment.status = status
        self.db.commit()
        self.db.refresh(payment)
        return payment
