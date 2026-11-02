import logging
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import Payment, PaymentMethod
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.payment_models import PaymentCreate as GatewayPaymentCreate, PaymentPayer
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from schemas.payments import PaymentCreate, PaymentWithSavedMethodCreate

logger = logging.getLogger(__name__)

SETTLED_STATUSES = ("approved", "rejected", "cancelled", "refunded", "charged_back")
DECLINED_STATUSES = ("rejected", "cancelled")


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

    @staticmethod
    def _cents(amount) -> Decimal:
        return Decimal(str(amount)).quantize(Decimal("0.01"))

    def _attempts(self, external_reference: str) -> list[Payment]:
        return (
            self._scoped(
                self.db.query(Payment).filter(Payment.external_reference == external_reference)
            )
            .order_by(Payment.id)
            .all()
        )

    def _already_charged(self, external_reference: str, user_id: str, amount) -> Payment | None:
        attempts = self._attempts(external_reference)
        if not attempts:
            return None
        opened = attempts[0]
        if opened.user_id != user_id or self._cents(opened.amount) != self._cents(amount):
            raise ValueError("payment_reference_conflict")
        standing = [
            payment for payment in attempts if payment.status not in DECLINED_STATUSES
        ]
        return standing[-1] if standing else None

    def _attempt_number(self, external_reference: str) -> int:
        return max(len(self._attempts(external_reference)), 1)

    def _idempotency_key(self, reference: str, attempt: int) -> str:
        key = f"payment:{self.tenant or 'default'}:{reference}"
        return key if attempt <= 1 else f"{key}#{attempt}"

    def _reserve(self, data, amount) -> tuple[Payment | None, Payment | None]:
        payment = Payment(
            tenant=self.tenant or "default",
            gateway="mercadopago",
            amount=amount,
            currency="ARS",
            status="pending",
            user_id=data.user_id,
            external_reference=data.external_reference,
            description=getattr(data, "description", None),
        )
        self.db.add(payment)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            return None, self._already_charged(
                data.external_reference, data.user_id, amount
            )
        return payment, None

    def create_payment(self, data: PaymentCreate) -> Payment:
        charged = self._already_charged(
            data.external_reference, data.user_id, data.transaction_amount
        )
        if charged:
            return charged
        payment, conceded = self._reserve(data, data.transaction_amount)
        if payment is None:
            if conceded is None:
                raise ValueError("payment_reference_conflict")
            return conceded
        reference = data.external_reference
        attempt = self._attempt_number(reference)
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
                gateway_data, idempotency_key=self._idempotency_key(reference, attempt)
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

    def create_payment_with_saved_method(self, data: PaymentWithSavedMethodCreate) -> Payment:
        charged = self._already_charged(
            data.external_reference, data.user_id, data.transaction_amount
        )
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

        payment, conceded = self._reserve(data, data.transaction_amount)
        if payment is None:
            if conceded is None:
                raise ValueError("payment_reference_conflict")
            return conceded
        reference = data.external_reference
        attempt = self._attempt_number(reference)
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
                gateway_data, idempotency_key=self._idempotency_key(reference, attempt)
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

    @staticmethod
    def preference_total(items) -> Decimal:
        total = sum(
            (Decimal(str(item.unit_price)) * Decimal(item.quantity) for item in items),
            Decimal("0"),
        )
        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def create_preference(self, data) -> dict:
        total = self.preference_total(data.items)
        opened = self._already_charged(data.external_reference, data.user_id, total)
        payment = None
        if not opened:
            payment, conceded = self._reserve(data, total)
            if payment is None:
                if conceded is None:
                    raise ValueError("payment_reference_conflict")
                opened = conceded

        items = [
            {
                "title": item.title,
                "quantity": item.quantity,
                "unit_price": float(Decimal(str(item.unit_price)).quantize(Decimal("0.01"))),
                "currency_id": item.currency_id,
            }
            for item in data.items
        ]
        back_urls = {
            "success": data.back_urls.success,
            "failure": data.back_urls.failure,
            "pending": data.back_urls.pending,
        }
        try:
            result = self.mp.create_preference(
                items=items,
                payer_email=data.payer_email,
                external_reference=data.external_reference,
                back_urls=back_urls,
                notification_url=data.notification_url,
            )
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        self.db.commit()
        return {
            "preference_id": result.get("id", ""),
            "init_point": result.get("init_point", ""),
            "sandbox_init_point": result.get("sandbox_init_point", ""),
            "payment_id": (payment or opened).id if (payment or opened) else None,
        }

    def record_gateway_payment(self, provider_data: dict) -> Payment | None:
        gateway_payment_id = str(provider_data.get("id") or "")
        if not gateway_payment_id:
            return None
        status = provider_data.get("status") or ""
        bound = self.update_payment_status(gateway_payment_id, status)
        if bound:
            return bound
        reference = provider_data.get("external_reference")
        if not reference:
            return None
        waiting = [
            payment
            for payment in self._attempts(reference)
            if not payment.gateway_payment_id and payment.status not in DECLINED_STATUSES
        ]
        if not waiting:
            return None
        payment = waiting[-1]
        charged = provider_data.get("transaction_amount")
        if charged is not None and self._cents(charged) != self._cents(payment.amount):
            logger.warning(
                "Gateway payment amount does not match the reference it names"
                " gateway_payment=%s reference=%s expected=%s charged=%s",
                gateway_payment_id,
                reference,
                payment.amount,
                charged,
            )
            return None
        payment.gateway_payment_id = gateway_payment_id
        if status:
            payment.status = status
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def get_payment(self, payment_id: int, user_id: str | None = None) -> Payment | None:
        query = self._scoped(self.db.query(Payment).filter(Payment.id == payment_id))
        if user_id is not None:
            query = query.filter(Payment.user_id == user_id)
        return query.first()

    def _rows_for_gateway_payment(self, gateway_payment_id: str) -> list[Payment]:
        return (
            self._scoped(
                self.db.query(Payment).filter(Payment.gateway_payment_id == gateway_payment_id)
            )
            .order_by(Payment.id)
            .all()
        )

    def get_payment_by_gateway_id(self, gateway_payment_id: str) -> Payment | None:
        rows = self._rows_for_gateway_payment(gateway_payment_id)
        return rows[0] if rows else None

    def update_payment_status(self, gateway_payment_id: str, status: str) -> Payment | None:
        payments = self._rows_for_gateway_payment(gateway_payment_id)
        if not payments:
            return None
        if not status:
            return payments[0]
        update = self._scoped(
            self.db.query(Payment).filter(Payment.gateway_payment_id == gateway_payment_id)
        )
        if status not in SETTLED_STATUSES:
            update = update.filter(Payment.status.notin_(SETTLED_STATUSES))
        applied = update.update({Payment.status: status}, synchronize_session=False)
        self.db.commit()
        if applied < len(payments):
            logger.info(
                "Ignored stale payment status gateway_payment=%s incoming=%s rows=%s applied=%s",
                gateway_payment_id,
                status,
                len(payments),
                applied,
            )
        return self._rows_for_gateway_payment(gateway_payment_id)[0]
