from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db.models import Payment, PaymentMethod
from db.session import Base
from schemas.payments import (
    PaymentCreate,
    PaymentPayer,
    PaymentWithSavedMethodCreate,
    PreferenceBackUrls,
    PreferenceCreate,
    PreferenceItem,
)
from services.payment_method_service import PaymentMethodService
from services.payment_service import PaymentService


class RecordingMercadoPago:
    def __init__(self, statuses=None):
        self.charges = []
        self.idempotency_keys = []
        self.statuses = list(statuses or [])
        self.by_key = {}

    def create_payment(self, data, idempotency_key=None):
        if idempotency_key in self.by_key:
            return self.by_key[idempotency_key]
        self.charges.append(data)
        self.idempotency_keys.append(idempotency_key)
        status = self.statuses.pop(0) if self.statuses else "approved"
        answer = {
            "id": f"mp-{len(self.charges)}",
            "status": status,
            "transaction_amount": data.transaction_amount,
        }
        self.by_key[idempotency_key] = answer
        return answer

    def get_customer_email(self, customer_id):
        return "saved@example.com"

    def create_card_token_from_saved(self, customer_id, card_id, security_code=None):
        return "fresh-token"


def _db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _card_payment(reference: str, user_id: str = "teacher-1", amount: float = 2500.0) -> PaymentCreate:
    return PaymentCreate(
        transaction_amount=amount,
        token="card-token",
        payment_method_id="visa",
        payer=PaymentPayer(email=f"{user_id}@example.com"),
        external_reference=reference,
        user_id=user_id,
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


@pytest.mark.parametrize(
    "missing",
    [
        {},
        {"external_reference": "", "user_id": "teacher-1"},
        {"external_reference": "   ", "user_id": "teacher-1"},
        {"external_reference": "order-7", "user_id": ""},
    ],
)
def test_a_payment_without_a_usable_reference_is_refused_by_the_schema(missing):
    with pytest.raises(ValidationError):
        PaymentCreate(
            transaction_amount=2500.0,
            token="card-token",
            payment_method_id="visa",
            payer=PaymentPayer(email="teacher@example.com"),
            **missing,
        )


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
                    external_reference="order-7",
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


def test_the_backfill_only_renames_rows_when_the_deployment_names_one_tenant():
    import importlib.util

    from config.settings import settings

    spec = importlib.util.spec_from_file_location(
        "m011", "alembic/versions/011_backfill_payment_tenant.py"
    )
    m011 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m011)

    original = (settings.api_keys_raw, settings.api_key)
    try:
        settings.api_keys_raw, settings.api_key = "", "legacy-key"
        assert m011.target_tenant() is None

        settings.api_keys_raw, settings.api_key = "practiq:k1", ""
        assert m011.target_tenant() == "practiq"

        settings.api_keys_raw, settings.api_key = "practiq:k1,yego:k2", ""
        assert m011.target_tenant() is None
    finally:
        settings.api_keys_raw, settings.api_key = original


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


def test_every_row_of_one_charge_settles_together():
    db = _db()
    try:
        for reference in ("order-7", "order-7-retry"):
            db.add(
                Payment(
                    tenant="practiq",
                    gateway_payment_id="mp-1",
                    amount=Decimal("100"),
                    status="pending",
                    user_id="teacher-1",
                    external_reference=reference,
                )
            )
        db.commit()

        PaymentService(db, RecordingMercadoPago(), tenant=None).update_payment_status(
            "mp-1", "approved"
        )

        assert [p.status for p in db.query(Payment).order_by(Payment.id).all()] == [
            "approved",
            "approved",
        ]
    finally:
        db.close()


def test_a_reference_belongs_to_one_payer():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        service = PaymentService(db, mp, tenant="practiq")
        service.create_payment(_card_payment("order-7", "ana", 5000.0))

        with pytest.raises(ValueError, match="payment_reference_conflict"):
            service.create_payment(_card_payment("order-7", "beto", 300.0))
        assert len(mp.charges) == 1
        assert db.query(Payment).count() == 1
    finally:
        db.close()


def test_a_reference_belongs_to_one_amount():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        service = PaymentService(db, mp, tenant="practiq")
        service.create_payment(_card_payment("order-9", "ana", 100.0))

        with pytest.raises(ValueError, match="payment_reference_conflict"):
            service.create_payment(_card_payment("order-9", "ana", 120.0))
        assert len(mp.charges) == 1
    finally:
        db.close()


def test_an_approved_order_is_not_recharged_after_a_later_decline():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        for status in ("approved", "rejected"):
            db.add(
                Payment(
                    tenant="practiq",
                    gateway_payment_id=f"mp-{status}",
                    amount=Decimal("2500"),
                    status=status,
                    user_id="teacher-1",
                    external_reference="order-7",
                )
            )
        db.commit()

        settled = PaymentService(db, mp, tenant="practiq").create_payment(_card_payment("order-7"))
        assert settled.status == "approved"
        assert mp.charges == []
    finally:
        db.close()


def test_a_settled_payment_does_not_regress_under_concurrent_webhooks():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        payment = PaymentService(db, mp, tenant="practiq").create_payment(_card_payment("order-7"))
        webhook = PaymentService(db, mp, tenant=None)

        stale = db.query(Payment).filter(Payment.id == payment.id).one()
        assert stale.status == "approved"
        assert webhook.update_payment_status(payment.gateway_payment_id, "pending").status == "approved"
        db.refresh(stale)
        assert stale.status == "approved"
    finally:
        db.close()


def test_the_database_allows_only_one_live_payment_per_reference():
    db = _db()
    try:
        for _ in range(2):
            db.add(
                Payment(
                    tenant="practiq",
                    amount=Decimal("100"),
                    status="pending",
                    user_id="teacher-1",
                    external_reference="order-7",
                )
            )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_a_rejected_payment_leaves_the_reference_free():
    db = _db()
    try:
        db.add(
            Payment(
                tenant="practiq",
                amount=Decimal("100"),
                status="rejected",
                user_id="teacher-1",
                external_reference="order-7",
            )
        )
        db.commit()
        db.add(
            Payment(
                tenant="practiq",
                amount=Decimal("100"),
                status="pending",
                user_id="teacher-1",
                external_reference="order-7",
            )
        )
        db.commit()
        assert db.query(Payment).count() == 2
    finally:
        db.close()


def test_a_racing_request_concedes_to_the_reservation_without_charging():
    db = _db()
    mp = RecordingMercadoPago()
    try:
        service = PaymentService(db, mp, tenant="practiq")
        winner = service.create_payment(_card_payment("order-7"))

        db.add(
            Payment(
                tenant="practiq",
                amount=Decimal("2500"),
                status="pending",
                user_id="teacher-1",
                external_reference="order-7",
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        assert service.create_payment(_card_payment("order-7")).id == winner.id
        assert len(mp.charges) == 1
    finally:
        db.close()


class PreferenceMercadoPago(RecordingMercadoPago):
    def create_preference(self, items, payer_email, external_reference, back_urls, notification_url=None):
        self.charges.append(items)
        return {"id": "pref-1", "init_point": "https://mp/checkout", "sandbox_init_point": ""}


def _preference(reference: str = "order-7", user_id: str = "teacher-1", unit_price: float = 1250.0):
    return PreferenceCreate(
        items=[PreferenceItem(title="Plan", quantity=2, unit_price=unit_price)],
        payer_email=f"{user_id}@example.com",
        external_reference=reference,
        user_id=user_id,
        back_urls=PreferenceBackUrls(success="https://x/ok", failure="https://x/no", pending="https://x/wait"),
    )


def test_a_checkout_pro_preference_opens_a_payment_row():
    db = _db()
    mp = PreferenceMercadoPago()
    try:
        answer = PaymentService(db, mp, tenant="practiq").create_preference(_preference())

        row = db.query(Payment).one()
        assert answer["payment_id"] == row.id
        assert (row.status, row.user_id, row.tenant) == ("pending", "teacher-1", "practiq")
        assert Decimal(str(row.amount)) == Decimal("2500.00")
        assert row.gateway_payment_id is None
    finally:
        db.close()


def test_the_webhook_binds_a_checkout_pro_payment_to_its_preference():
    db = _db()
    mp = PreferenceMercadoPago()
    try:
        PaymentService(db, mp, tenant="practiq").create_preference(_preference())

        bound = PaymentService(db, mp, tenant=None).record_gateway_payment(
            {
                "id": "mp-77",
                "status": "approved",
                "external_reference": "order-7",
                "transaction_amount": 2500.0,
            }
        )

        assert bound is not None
        assert (bound.gateway_payment_id, bound.status) == ("mp-77", "approved")
        assert db.query(Payment).count() == 1
    finally:
        db.close()


def test_a_checkout_pro_payment_for_another_amount_is_not_bound():
    db = _db()
    mp = PreferenceMercadoPago()
    try:
        PaymentService(db, mp, tenant="practiq").create_preference(_preference())

        assert (
            PaymentService(db, mp, tenant=None).record_gateway_payment(
                {
                    "id": "mp-77",
                    "status": "approved",
                    "external_reference": "order-7",
                    "transaction_amount": 99.0,
                }
            )
            is None
        )
        assert db.query(Payment).one().gateway_payment_id is None
    finally:
        db.close()


def test_rebuilding_a_preference_reuses_the_row_it_already_opened():
    db = _db()
    mp = PreferenceMercadoPago()
    try:
        service = PaymentService(db, mp, tenant="practiq")
        first = service.create_preference(_preference())
        again = service.create_preference(_preference())

        assert again["payment_id"] == first["payment_id"]
        assert db.query(Payment).count() == 1
    finally:
        db.close()


def test_a_preference_for_a_reference_owned_by_another_payer_is_refused():
    db = _db()
    mp = PreferenceMercadoPago()
    try:
        service = PaymentService(db, mp, tenant="practiq")
        service.create_preference(_preference(user_id="ana"))
        with pytest.raises(ValueError, match="payment_reference_conflict"):
            service.create_preference(_preference(user_id="beto"))
    finally:
        db.close()
