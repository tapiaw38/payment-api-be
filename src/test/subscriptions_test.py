from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.v1.routers.webhooks import _validate_signature
from config.settings import settings
from db.models import Plan, Subscription
from db.session import Base
from schemas.subscriptions import BillingCycleCreate
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
                active_seats=4,
                unit_amount=Decimal("2500"),
                minimum_amount=Decimal("25000"),
            ),
        )

        assert cycle.amount == Decimal("25000.00")
        assert cycle.active_seats == 4
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
