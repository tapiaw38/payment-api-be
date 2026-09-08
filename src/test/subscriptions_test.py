from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.v1.routers.webhooks import _validate_signature
from config.settings import settings
from db.models import Plan, Subscription
from db.session import Base
from schemas.subscriptions import BillingCycleCreate, PlanCreate, PlanResponse
from services.subscription_service import SubscriptionService


class NoopMercadoPago:
    pass


def test_billing_cycle_uses_minimum_amount():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        plan = Plan(name="Practiq", amount=Decimal("2500"), interval="month")
        db.add(plan)
        db.flush()
        subscription = Subscription(plan_id=plan.id, user_id="teacher-1", gateway_subscription_id="preapproval-1")
        db.add(subscription)
        db.commit()

        cycle = SubscriptionService(db, NoopMercadoPago()).create_billing_cycle(
            subscription.id,
            BillingCycleCreate(
                period_start=datetime(2026, 9, 1),
                period_end=datetime(2026, 10, 1),
                quantity=4,
                unit_amount=Decimal("2500"),
                minimum_amount=Decimal("25000"),
            ),
        )

        assert cycle.amount == Decimal("25000.00")
        assert cycle.quantity == 4
    finally:
        db.close()
        engine.dispose()


def test_webhook_signature_requires_matching_hmac():
    secret = "test-webhook-secret"
    previous = settings.mercadopago_webhook_secret
    settings.mercadopago_webhook_secret = secret
    try:
        import hashlib
        import hmac

        data_id, request_id, timestamp = "ABC-123", "request-1", "1704908010"
        manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
        digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        request = SimpleNamespace(headers={"x-request-id": request_id, "x-signature": f"ts={timestamp},v1={digest}"})

        assert _validate_signature(request, data_id)
        assert not _validate_signature(request, "ABC-124")
    finally:
        settings.mercadopago_webhook_secret = previous


def test_plan_metadata_is_stored_and_returned_untouched():
    """The service must not interpret what a product puts in a plan.

    This is what keeps product rules out of here: a caller can describe what a
    plan grants in its own vocabulary, and nothing in this codebase has to
    learn that vocabulary to store it.
    """
    granted = {"max_students": 5, "features": ["reports"], "nested": {"a": 1}}

    # id/active/created_at are set by the database, and from_orm demands them.
    plan = Plan(
        id=1,
        name="Team",
        amount=25000,
        currency="ARS",
        interval="month",
        interval_count=1,
        gateway="mercadopago",
        plan_metadata=granted,
        active=1,
        created_at=datetime.utcnow(),
    )

    assert PlanResponse.from_orm(plan).metadata == granted


def test_plan_without_metadata_reports_an_empty_object():
    plan = Plan(
        id=2,
        name="Free",
        amount=0,
        currency="ARS",
        interval="month",
        interval_count=1,
        active=1,
        created_at=datetime.utcnow(),
    )
    assert PlanResponse.from_orm(plan).metadata == {}


def test_a_product_cannot_read_another_products_rows():
    """`user_id` is an opaque string from each product's own auth.

    Two products can hand us the same one, so without the tenant filter a
    lookup by user id answers with whatever row it finds first — which may
    belong to somebody else entirely.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        for tenant in ("practiq", "yego"):
            plan = Plan(tenant=tenant, name=f"{tenant} plan", amount=Decimal("1000"), interval="month")
            db.add(plan)
            db.flush()
            db.add(
                Subscription(
                    tenant=tenant,
                    plan_id=plan.id,
                    # The same id in both products, which is the whole point.
                    user_id="user-1",
                    status="authorized",
                    gateway_subscription_id=f"preapproval-{tenant}",
                )
            )
        db.commit()

        practiq = SubscriptionService(db, NoopMercadoPago(), tenant="practiq")
        yego = SubscriptionService(db, NoopMercadoPago(), tenant="yego")

        assert practiq.get_entitlement("user-1").tenant == "practiq"
        assert yego.get_entitlement("user-1").tenant == "yego"

        assert [s.tenant for s in practiq.get_subscription_by_user("user-1")] == ["practiq"]
        assert [p.name for p in practiq.list_plans(active_only=False)] == ["practiq plan"]

        # Reaching for another product's row by its primary key finds nothing.
        yego_subscription_id = yego.get_entitlement("user-1").id
        assert practiq.get_subscription(yego_subscription_id) is None
    finally:
        db.close()


class RecordingMercadoPago:
    """Records what was asked of the gateway, so a test can assert the call."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def pause_subscription(self, preapproval_id: str) -> dict:
        self.calls.append(("pause", preapproval_id))
        return {"status": "paused"}

    def resume_subscription(self, preapproval_id: str) -> dict:
        self.calls.append(("resume", preapproval_id))
        return {"status": "authorized"}


def _subscription_db(status: str):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    plan = Plan(tenant="practiq", name="Equipo", amount=Decimal("25000"), interval="month")
    db.add(plan)
    db.flush()
    db.add(
        Subscription(
            tenant="practiq",
            plan_id=plan.id,
            user_id="teacher-1",
            status=status,
            gateway_subscription_id="preapproval-1",
        )
    )
    db.commit()
    return db


def test_pausing_stops_charges_and_can_be_undone():
    db = _subscription_db("authorized")
    mp = RecordingMercadoPago()
    service = SubscriptionService(db, mp, tenant="practiq")
    subscription_id = service.get_entitlement("teacher-1").id
    try:
        assert service.pause_subscription(subscription_id).status == "paused"
        assert service.resume_subscription(subscription_id).status == "authorized"
        assert mp.calls == [("pause", "preapproval-1"), ("resume", "preapproval-1")]
    finally:
        db.close()


def test_a_cancelled_subscription_cannot_be_resumed():
    """Cancelling withdraws the payer's authorisation at the gateway.

    Offering resume on a cancelled subscription would promise something only
    the payer's card details can deliver, which is exactly why the product
    offers pausing before it offers cancelling.
    """
    db = _subscription_db("cancelled")
    mp = RecordingMercadoPago()
    service = SubscriptionService(db, mp, tenant="practiq")
    subscription = db.query(Subscription).first()
    try:
        raised = False
        try:
            service.resume_subscription(subscription.id)
        except ValueError as exc:
            raised = str(exc) == "subscription_not_paused"
        assert raised, "resuming a cancelled subscription must be refused"
        assert mp.calls == [], "the gateway must not be called at all"
    finally:
        db.close()


class PlanRecordingMercadoPago:
    def __init__(self):
        self.body: dict = {}

    def create_plan(self, **kwargs) -> dict:
        self.body = kwargs
        return {"id": "preapproval-plan-1"}


def test_publishing_a_plan_sends_the_back_url_the_gateway_requires():
    """The gateway rejects a plan with no back_url.

    It answers "Parameters passed are invalid", which names nothing, so this
    failed silently for every plan until the request was cut down until the
    gateway said what it actually wanted.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    mp = PlanRecordingMercadoPago()
    service = SubscriptionService(
        db, mp, back_url="https://practiq.example/teacher/subscription", tenant="practiq"
    )
    try:
        service.create_plan(
            PlanCreate(name="Equipo", amount=25000, interval="month", metadata={"max_students": 5})
        )
        assert mp.body["back_url"] == "https://practiq.example/teacher/subscription"
    finally:
        db.close()


def test_a_plan_is_refused_when_no_back_url_is_configured():
    """Better a loud failure than a plan row pointing at no gateway plan."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    mp = PlanRecordingMercadoPago()
    service = SubscriptionService(db, mp, tenant="practiq")
    try:
        raised = False
        try:
            service.create_plan(PlanCreate(name="Equipo", amount=25000, interval="month"))
        except ValueError as exc:
            raised = str(exc) == "back_url_not_configured"
        assert raised
        assert mp.body == {}, "the gateway must not be called at all"
    finally:
        db.close()
