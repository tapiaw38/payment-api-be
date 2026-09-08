from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.v1.routers.webhooks import _validate_signature
from config.settings import settings
from db.models import Plan, Subscription
from db.session import Base
from schemas.subscriptions import BillingCycleCreate, PlanResponse
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
