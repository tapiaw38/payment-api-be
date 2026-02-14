import logging
from sqlalchemy.orm import Session

from db.models import Payment, PaymentMethod
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.payment_models import PaymentCreate as GatewayPaymentCreate
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from schemas.payments import PaymentCreate, PaymentWithSavedMethodCreate

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, db: Session, mp_payment: MercadopagoPaymentService):
        self.db = db
        self.mp = mp_payment

    def create_payment(self, data: PaymentCreate) -> Payment:
        payment = Payment(
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
        try:
            gateway_data = GatewayPaymentCreate(
                transaction_amount=data.transaction_amount,
                token=data.token,
                payment_method_id=data.payment_method_id,
                payer=data.payer,
                installments=data.installments,
                description=data.description,
                external_reference=data.external_reference or str(payment.id),
            )
            result = self.mp.create_payment(gateway_data)
            payment.gateway_payment_id = str(result.get("id", ""))
            payment.status = result.get("status", "pending")
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def create_payment_with_saved_method(self, data: PaymentWithSavedMethodCreate) -> Payment:
        print(f"[DEBUG] Creating payment with saved method for user {data.user_id}, payment_method_id {data.payment_method_id}", flush=True)
        payment_method = (
            self.db.query(PaymentMethod)
            .filter(
                PaymentMethod.id == data.payment_method_id,
                PaymentMethod.user_id == data.user_id,
                PaymentMethod.is_default == 1
            )
            .first()
        )

        if not payment_method:
            print(f"[ERROR] Payment method not found for user {data.user_id}, payment_method_id {data.payment_method_id}", flush=True)
            raise ValueError("Payment method not found or not default")

        print(f"[DEBUG] Found payment method: mp_customer_id={payment_method.mp_customer_id}, mp_card_id={payment_method.mp_card_id}", flush=True)

        # Get the correct payer email from MP customer (not from request which might be admin's email)
        payer_email = data.payer.email
        if payment_method.mp_customer_id:
            customer_email = self.mp.get_customer_email(payment_method.mp_customer_id)
            if customer_email:
                print(f"[DEBUG] Using MP customer email: {customer_email} (instead of request email: {payer_email})", flush=True)
                payer_email = customer_email

        # Use Customer Cards API for a fresh token when available; fall back to the stored token
        if payment_method.mp_customer_id and payment_method.mp_card_id:
            print(f"[DEBUG] Creating fresh token from customer card", flush=True)
            fresh_token = self.mp.create_card_token_from_saved(
                payment_method.mp_customer_id,
                payment_method.mp_card_id,
                data.security_code,
            )
            print(f"[DEBUG] Fresh token created: {fresh_token}", flush=True)
        else:
            print(f"[DEBUG] Using stored token: {payment_method.card_token_id}", flush=True)
            fresh_token = payment_method.card_token_id

        payment = Payment(
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
        try:
            # Create payer object with correct email and customer info
            from gateways.mercadopago.payment_models import PaymentPayer
            payer_info = PaymentPayer(
                email=payer_email,
                type="customer",
                id=payment_method.mp_customer_id
            )

            gateway_data = GatewayPaymentCreate(
                transaction_amount=data.transaction_amount,
                token=fresh_token,
                payment_method_id=payment_method.payment_method_id,
                payer=payer_info,
                installments=data.installments,
                description=data.description,
                external_reference=data.external_reference or str(payment.id),
                collector_id=data.collector_id,
            )
            print(f"[DEBUG] Creating MP payment with: amount={data.transaction_amount}, token={fresh_token[:20]}..., payment_method_id={payment_method.payment_method_id}, payer_email={payer_email}, collector_id={data.collector_id}", flush=True)
            result = self.mp.create_payment(gateway_data)
            print(f"[DEBUG] MP payment created successfully: {result.get('id')}", flush=True)
            payment.gateway_payment_id = str(result.get("id", ""))
            payment.status = result.get("status", "pending")
        except MercadopagoAPIException as e:
            print(f"[ERROR] MP payment creation failed: {e}", flush=True)
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(payment)
        return payment

    def get_payment(self, payment_id: int) -> Payment | None:
        return self.db.query(Payment).filter(Payment.id == payment_id).first()

    def get_payment_by_gateway_id(self, gateway_payment_id: str) -> Payment | None:
        return (
            self.db.query(Payment)
            .filter(Payment.gateway_payment_id == gateway_payment_id)
            .first()
        )

    def update_payment_status(self, gateway_payment_id: str, status: str) -> Payment | None:
        payment = self.get_payment_by_gateway_id(gateway_payment_id)
        if not payment:
            return None
        payment.status = status
        self.db.commit()
        self.db.refresh(payment)
        return payment
