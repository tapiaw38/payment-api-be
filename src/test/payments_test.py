from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from db.models import Payment, PaymentMethod
from db.session import Base
from schemas.payments import PaymentCreate, PaymentPayer, PaymentWithSavedMethodCreate
from services.payment_method_service import PaymentMethodService
from services.payment_service import PaymentService


class RecordingMercadoPago:
    def __init__(self, statuses=None):
        self.charges = []
        self.idempotency_keys = []
        self.statuses = list(statuses or [])

    def create_payment(self, data, idempotency_key=None):
        self.charges.append(data)
        self.idempotency_keys.append(idempotency_key)
        status = self.statuses.pop(0) if self.statuses else "approved"
        return {"id": f"mp-{len(self.charges)}", "status": status}

    def get_customer_email(self, customer_id):
        return "saved@example.com"

    def create_card_token_from_saved(self, customer_id, card_id, security_code=None):
        return "fresh-token"


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _card_payment(reference: str | None) -> PaymentCreate:
    return PaymentCreate(
        transaction_amount=2500.0,
        token="card-token",
        payment_method_id="visa",
        payer=PaymentPayer(email="teacher@example.com"),
        external_reference=reference,
        user_id="teacher-1",
    )


def test_resubmitting_the_same_purchase_does_not_charge_twice():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        service = PaymentService(db, mp, tenant="practiq")
        first = service.create_payment(_card_payment("order-7"))
        second = service.create_payment(_card_payment("order-7"))

        assert second.id == first.id
        assert len(mp.charges) == 1
        assert db.query(Payment).count() == 1
    finally:
        db.close()


def test_a_charge_carries_an_idempotency_key_the_gateway_can_collapse():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        PaymentService(db, mp, tenant="practiq").create_payment(_card_payment("order-7"))
        assert mp.idempotency_keys == ["payment:practiq:order-7"]
    finally:
        db.close()


def test_a_payment_without_a_reference_still_sends_a_stable_key():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        payment = PaymentService(db, mp, tenant="practiq").create_payment(_card_payment(None))
        assert mp.idempotency_keys == [f"payment:practiq:{payment.id}"]
    finally:
        db.close()


def test_a_product_cannot_read_another_products_payments():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        practiq = PaymentService(db, mp, tenant="practiq")
        yego = PaymentService(db, mp, tenant="yego")
        practiq.create_payment(_card_payment("order-practiq"))
        yego.create_payment(_card_payment("order-yego"))

        theirs = db.query(Payment).filter(Payment.tenant == "yego").one()
        assert practiq.get_payment(theirs.id) is None
        assert practiq.get_payment_by_gateway_id(theirs.gateway_payment_id) is None

        charges_before = len(mp.charges)
        practiq.create_payment(_card_payment("order-yego"))
        assert len(mp.charges) == charges_before + 1
    finally:
        db.close()


def test_a_payment_is_not_readable_by_another_user():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        service = PaymentService(db, mp, tenant="practiq")
        payment = service.create_payment(_card_payment("order-7"))

        assert service.get_payment(payment.id, user_id="teacher-1") is not None
        assert service.get_payment(payment.id, user_id="teacher-2") is None
    finally:
        db.close()


def test_a_saved_card_belonging_to_another_product_is_not_chargeable():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        card = PaymentMethod(
            tenant="yego",
            user_id="teacher-1",
            card_token_id="token",
            last_four_digits="4242",
            payment_method_id="visa",
            cardholder_name="A B",
            expiration_month="12",
            expiration_year="2030",
            is_default=1,
        )
        db.add(card)
        db.commit()

        service = PaymentService(db, mp, tenant="practiq")
        try:
            service.create_payment_with_saved_method(
                PaymentWithSavedMethodCreate(
                    transaction_amount=2500.0,
                    payment_method_id=card.id,
                    payer=PaymentPayer(email="teacher@example.com"),
                    user_id="teacher-1",
                )
            )
            raise AssertionError("another product's card must not be chargeable")
        except ValueError as exc:
            assert "not found" in str(exc)
        assert mp.charges == []
    finally:
        db.close()


def test_a_late_pending_webhook_does_not_unpay_an_approved_payment():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        payment = PaymentService(db, mp, tenant="practiq").create_payment(_card_payment("order-7"))
        assert payment.status == "approved"

        webhook = PaymentService(db, mp, tenant=None)
        assert webhook.update_payment_status(payment.gateway_payment_id, "pending").status == "approved"
        assert webhook.update_payment_status(payment.gateway_payment_id, "").status == "approved"
        assert webhook.update_payment_status(payment.gateway_payment_id, "refunded").status == "refunded"
    finally:
        db.close()


def test_the_webhook_reaches_a_payment_of_any_tenant():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        payment = PaymentService(db, mp, tenant="yego").create_payment(_card_payment("order-7"))
        updated = PaymentService(db, mp, tenant=None).update_payment_status(
            payment.gateway_payment_id, "refunded"
        )
        assert updated is not None and updated.tenant == "yego"
    finally:
        db.close()


def test_amounts_are_stored_to_the_cent():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        payment = PaymentService(db, mp, tenant="practiq").create_payment(_card_payment("order-7"))
        assert Decimal(str(payment.amount)) == Decimal("2500.00")
    finally:
        db.close()


def test_backfill_gives_old_rows_the_tenant_that_owns_them():
    import importlib.util

    from db.models import Plan, Subscription

    spec = importlib.util.spec_from_file_location(
        "m011", "alembic/versions/011_backfill_payment_tenant.py"
    )
    m011 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m011)

    db = _db()
    try:
        plan = Plan(tenant="practiq", name="Practiq", amount=Decimal("2500"), interval="month")
        db.add(plan)
        db.flush()
        db.add(
            Subscription(tenant="practiq", plan_id=plan.id, user_id="teacher-1", status="authorized")
        )
        db.add(Payment(tenant="default", amount=Decimal("2500"), status="approved", user_id="teacher-1"))
        db.add(Payment(tenant="default", amount=Decimal("900"), status="approved", user_id="stranger"))
        db.add(Payment(tenant="yego", amount=Decimal("100"), status="approved", user_id="teacher-1"))
        db.commit()

        for table in ("payments", "payment_methods"):
            db.execute(text(m011.SQLITE_BACKFILL.format(table=table)))
        db.commit()

        by_user = {p.user_id: p.tenant for p in db.query(Payment).filter(Payment.amount != 100).all()}
        assert by_user == {"teacher-1": "practiq", "stranger": "default"}
        assert db.query(Payment).filter(Payment.amount == 100).one().tenant == "yego"
    finally:
        db.close()


def test_a_declined_card_can_be_retried_on_the_same_order():
    db = _db()
    mp = RecordingMercadoPago(statuses=["rejected", "approved"])
    try:
        service = PaymentService(db, mp, tenant="practiq")
        declined = service.create_payment(_card_payment("order-7"))
        assert declined.status == "rejected"

        retried = service.create_payment(_card_payment("order-7"))
        assert retried.status == "approved"
        assert retried.id != declined.id
        assert mp.idempotency_keys == ["payment:practiq:order-7", "payment:practiq:order-7#2"]
    finally:
        db.close()


def test_an_approved_order_is_not_charged_again_after_a_decline():
    db = _db()
    mp = RecordingMercadoPago(statuses=["rejected", "approved"])
    try:
        service = PaymentService(db, mp, tenant="practiq")
        service.create_payment(_card_payment("order-7"))
        approved = service.create_payment(_card_payment("order-7"))
        again = service.create_payment(_card_payment("order-7"))

        assert again.id == approved.id
        assert len(mp.charges) == 2
    finally:
        db.close()


def test_a_pending_order_is_not_charged_again_while_it_settles():
    db = _db()
    mp = RecordingMercadoPago(statuses=["in_process"])
    try:
        service = PaymentService(db, mp, tenant="practiq")
        pending = service.create_payment(_card_payment("order-7"))
        assert service.create_payment(_card_payment("order-7")).id == pending.id
        assert len(mp.charges) == 1
    finally:
        db.close()


def test_making_a_second_card_the_default_unsets_the_first():
    db = _db()
    try:
        service = PaymentMethodService(db, tenant="practiq")
        card = SimpleNamespace(
            is_default=True,
            card_token_id="token",
            last_four_digits="4242",
            payment_method_id="visa",
            cardholder_name="A B",
            expiration_month="12",
            expiration_year="2030",
            payer_email=None,
            card_number=None,
            security_code=None,
            doc_type=None,
            doc_number=None,
        )
        first = service.create_payment_method("teacher-1", card)
        second = service.create_payment_method("teacher-1", card)

        db.refresh(first)
        assert (first.is_default, second.is_default) == (0, 1)
        assert service.get_default_payment_method("teacher-1").id == second.id
    finally:
        db.close()


def test_a_charge_recorded_twice_by_a_race_settles_on_both_rows():
    db = _db()
    try:
        for _ in range(2):
            db.add(
                Payment(
                    tenant="practiq",
                    gateway_payment_id="mp-1",
                    amount=Decimal("100"),
                    status="pending",
                    user_id="teacher-1",
                    external_reference="order-7",
                )
            )
        db.commit()

        PaymentService(db, RecordingMercadoPago(), tenant=None).update_payment_status(
            "mp-1", "approved"
        )

        rows = db.query(Payment).order_by(Payment.id).all()
        assert [p.status for p in rows] == ["approved", "approved"]
    finally:
        db.close()
