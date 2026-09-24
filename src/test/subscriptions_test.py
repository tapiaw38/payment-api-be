from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.v1.routers.webhooks import _validate_signature
from config.settings import settings
from db.models import Plan, Subscription
from db.session import Base
from gateways.mercadopago.exceptions import MercadopagoAPIException
from schemas.subscriptions import (
    BillingCycleCreate,
    HostedSubscriptionCreate,
    PlanChangeCreate,
    PlanCreate,
    PlanResponse,
    SubscriptionCreate,
)
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

        # Unpacked, not truth-tested: the function returns (valid, reason), and
        # a non-empty tuple is always truthy — so `assert not _validate(...)`
        # failed on a correct rejection while `assert _validate(...)` passed on
        # anything at all.
        valid, _ = _validate_signature(request, data_id)
        assert valid

        valid, reason = _validate_signature(request, "ABC-124")
        assert not valid
        assert reason == "signature_mismatch"
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


class _DeclinedResponse:
    """The exception reads the gateway's response, so a test needs one."""

    status_code = 400
    text = "card declined"

    def json(self):
        return {"message": "card declined", "code": "declined"}


class SwitchRecordingMercadoPago:
    def __init__(self, fail_create=False):
        self.cancelled: list[str] = []
        self.created = 0
        self.fail_create = fail_create

    def cancel_subscription(self, preapproval_id: str) -> dict:
        self.cancelled.append(preapproval_id)
        return {"status": "cancelled"}

    def create_subscription(self, **kwargs) -> dict:
        if self.fail_create:
            raise MercadopagoAPIException(_DeclinedResponse())
        self.created += 1
        return {"id": "preapproval-new", "status": "authorized"}


def _two_plan_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    for name, gw in (("Solo", "gw-solo"), ("Equipo", "gw-equipo")):
        db.add(Plan(tenant="practiq", name=name, amount=Decimal("25000"), interval="month", gateway_plan_id=gw))
    db.flush()
    first = db.query(Plan).order_by(Plan.id).first()
    db.add(
        Subscription(
            tenant="practiq",
            plan_id=first.id,
            user_id="teacher-1",
            status="authorized",
            gateway_subscription_id="preapproval-old",
        )
    )
    db.commit()
    return db


def test_changing_plan_cancels_the_previous_agreement():
    """Two live agreements at the gateway means the payer is charged twice."""
    db = _two_plan_db()
    mp = SwitchRecordingMercadoPago()
    service = SubscriptionService(db, mp, webhook_url="https://hook", tenant="practiq")
    target = db.query(Plan).order_by(Plan.id.desc()).first()
    try:
        service.create_subscription(
            SubscriptionCreate(
                plan_id=target.id, user_id="teacher-1", payer_email="t@example.com", card_token_id="tok"
            )
        )
        assert mp.cancelled == ["preapproval-old"], "the old agreement must be cancelled"
        live = [s for s in db.query(Subscription).all() if s.status in SubscriptionService.LIVE_STATUSES]
        assert len(live) == 1, f"exactly one live subscription, got {len(live)}"
        assert live[0].plan_id == target.id
    finally:
        db.close()


def test_a_declined_card_leaves_no_subscription_rather_than_two():
    """The old one is cancelled first, so a failure costs access, not money."""
    db = _two_plan_db()
    mp = SwitchRecordingMercadoPago(fail_create=True)
    service = SubscriptionService(db, mp, webhook_url="https://hook", tenant="practiq")
    target = db.query(Plan).order_by(Plan.id.desc()).first()
    try:
        raised = False
        try:
            service.create_subscription(
                SubscriptionCreate(
                    plan_id=target.id, user_id="teacher-1", payer_email="t@example.com", card_token_id="tok"
                )
            )
        except MercadopagoAPIException:
            raised = True
        assert raised
        live = [s for s in db.query(Subscription).all() if s.status in SubscriptionService.LIVE_STATUSES]
        assert len(live) == 0, "no agreement may survive a failed switch"
    finally:
        db.close()


def test_subscribing_again_to_the_same_plan_is_refused():
    """Otherwise a double click cancels a working subscription and rebuys it."""
    db = _two_plan_db()
    mp = SwitchRecordingMercadoPago()
    service = SubscriptionService(db, mp, webhook_url="https://hook", tenant="practiq")
    current = db.query(Plan).order_by(Plan.id).first()
    try:
        raised = False
        try:
            service.create_subscription(
                SubscriptionCreate(
                    plan_id=current.id, user_id="teacher-1", payer_email="t@example.com", card_token_id="tok"
                )
            )
        except ValueError as exc:
            raised = str(exc) == "already_subscribed_to_plan"
        assert raised
        assert mp.cancelled == [], "nothing may be cancelled"
        assert mp.created == 0
    finally:
        db.close()


class HostedMercadoPago:
    """Answers the planless preapproval call the hosted flow makes."""

    def __init__(self, init_point: str = "https://mp.test/checkout?preapproval_id=abc"):
        self.init_point = init_point
        self.calls: list[dict] = []

    def create_pending_subscription(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"id": "preapproval-hosted", "status": "pending", "init_point": self.init_point}


def test_hosted_checkout_returns_somewhere_to_send_the_payer():
    """No card token is involved: the payer authorises at the gateway."""
    db = _two_plan_db()
    # _two_plan_db leaves teacher-1 subscribed; this teacher is not.
    mp = HostedMercadoPago()
    service = SubscriptionService(
        db, mp, webhook_url="https://hook", back_url="https://app.test/back", tenant="practiq"
    )
    plan = db.query(Plan).order_by(Plan.id).first()
    try:
        sub, init_point = service.start_hosted_subscription(
            HostedSubscriptionCreate(plan_id=plan.id, user_id="teacher-2", payer_email="t2@example.com")
        )
        assert init_point == mp.init_point
        assert sub.status == "pending"
        assert sub.gateway_subscription_id == "preapproval-hosted"
        # The webhook finds our row by external_reference, so it has to be sent.
        assert mp.calls[0]["external_reference"] == str(sub.id)
        assert mp.calls[0]["notification_url"] == "https://hook"
    finally:
        db.close()


def test_hosted_checkout_refuses_somebody_already_subscribed():
    """Cancelling up front would leave a payer with nothing if they close the tab."""
    db = _two_plan_db()
    mp = HostedMercadoPago()
    service = SubscriptionService(
        db, mp, webhook_url="https://hook", back_url="https://app.test/back", tenant="practiq"
    )
    plan = db.query(Plan).order_by(Plan.id.desc()).first()
    try:
        raised = ""
        try:
            service.start_hosted_subscription(
                HostedSubscriptionCreate(plan_id=plan.id, user_id="teacher-1", payer_email="t@example.com")
            )
        except ValueError as e:
            raised = str(e.args[0])
        assert raised == "already_subscribed", raised
        assert mp.calls == [], "the gateway must not be called at all"
    finally:
        db.close()


class ResumableMercadoPago(HostedMercadoPago):
    """A gateway that remembers the agreement it opened."""

    def __init__(self, gateway_status: str = "pending"):
        super().__init__()
        self.gateway_status = gateway_status
        self.cancelled: list[str] = []

    def get_subscription(self, preapproval_id: str) -> dict:
        return {
            "id": preapproval_id,
            "status": self.gateway_status,
            "init_point": self.init_point,
        }

    def cancel_subscription(self, preapproval_id: str) -> dict:
        self.cancelled.append(preapproval_id)
        return {"status": "cancelled"}

    def create_subscription(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return {"id": "preapproval-card", "status": "authorized"}


def _hosted_service(db, mp):
    return SubscriptionService(
        db, mp, webhook_url="https://hook", back_url="https://app.test/back", tenant="practiq"
    )


def test_abandoning_the_gateway_does_not_lock_the_payer_out():
    """A pending agreement grants nothing, so it must not block paying by card.

    Hosted checkout leaves one behind every time somebody closes the tab at
    Mercado Pago. Counting it as a live subscription shut the teacher out of
    both doors: the hosted one said already_subscribed and the card one said
    already_subscribed_to_plan.
    """
    db = _two_plan_db()
    mp = ResumableMercadoPago()
    plan = db.query(Plan).order_by(Plan.id).first()
    try:
        _hosted_service(db, mp).start_hosted_subscription(
            HostedSubscriptionCreate(plan_id=plan.id, user_id="teacher-2", payer_email="t2@example.com")
        )
        # They give up on the gateway and reach for a card instead.
        sub = _hosted_service(db, mp).create_subscription(
            SubscriptionCreate(
                plan_id=plan.id, user_id="teacher-2", payer_email="t2@example.com", card_token_id="tok"
            )
        )
        assert sub.status == "authorized"
        assert mp.cancelled == ["preapproval-hosted"], "the abandoned agreement must be cancelled"
        live = [
            s
            for s in db.query(Subscription).filter(Subscription.user_id == "teacher-2").all()
            if s.status in SubscriptionService.LIVE_STATUSES
        ]
        assert len(live) == 1, f"exactly one live subscription, got {len(live)}"
    finally:
        db.close()


def test_clicking_again_returns_to_the_same_agreement():
    """Opening a second agreement for the same plan would be a second charge."""
    db = _two_plan_db()
    mp = ResumableMercadoPago()
    plan = db.query(Plan).order_by(Plan.id).first()
    try:
        service = _hosted_service(db, mp)
        first, first_point = service.start_hosted_subscription(
            HostedSubscriptionCreate(plan_id=plan.id, user_id="teacher-2", payer_email="t2@example.com")
        )
        again, again_point = _hosted_service(db, mp).start_hosted_subscription(
            HostedSubscriptionCreate(plan_id=plan.id, user_id="teacher-2", payer_email="t2@example.com")
        )
        assert again.id == first.id
        assert again_point == first_point
        assert len(mp.calls) == 1, "the gateway must not be asked for a second agreement"
    finally:
        db.close()


def test_a_pending_row_catches_up_when_the_payer_did_authorise():
    """The webhook may not have landed yet; the screen must not offer to pay twice."""
    db = _two_plan_db()
    mp = ResumableMercadoPago(gateway_status="authorized")
    plan = db.query(Plan).order_by(Plan.id).first()
    try:
        _hosted_service(db, mp).start_hosted_subscription(
            HostedSubscriptionCreate(plan_id=plan.id, user_id="teacher-2", payer_email="t2@example.com")
        )
        mp.gateway_status = "authorized"
        raised = ""
        try:
            _hosted_service(db, mp).start_hosted_subscription(
                HostedSubscriptionCreate(plan_id=plan.id, user_id="teacher-2", payer_email="t2@example.com")
            )
        except ValueError as e:
            raised = str(e.args[0])
        assert raised == "already_subscribed", raised
        assert mp.cancelled == [], "an authorised agreement must never be cancelled"
    finally:
        db.close()


def test_correcting_the_email_opens_a_new_agreement():
    """Mercado Pago refuses its checkout to anyone but the payer_email's owner.

    Retyping the right address is how somebody recovers from that, so going
    back to the agreement that already rejected them would be a dead end. It
    never gives the address back, which is why the row keeps it.
    """
    db = _two_plan_db()
    mp = ResumableMercadoPago()
    plan = db.query(Plan).order_by(Plan.id).first()
    try:
        first, _ = _hosted_service(db, mp).start_hosted_subscription(
            HostedSubscriptionCreate(
                plan_id=plan.id, user_id="teacher-2", payer_email="practiq@example.com"
            )
        )
        second, _ = _hosted_service(db, mp).start_hosted_subscription(
            HostedSubscriptionCreate(
                plan_id=plan.id, user_id="teacher-2", payer_email="mercadopago@example.com"
            )
        )
        assert second.id != first.id, "a different address needs its own agreement"
        assert mp.cancelled == ["preapproval-hosted"], "the rejected one must be cancelled"
        assert second.payer_email == "mercadopago@example.com"
    finally:
        db.close()


def test_paid_time_survives_a_pause():
    """Pausing stops the next charge, not the month already bought.

    Pausing on the 9th with access paid to the 24th used to drop the teacher
    to the free plan that instant, costing two weeks they had already paid
    for and deactivating students over the free limit.
    """
    db = _subscription_db("paused")
    sub = db.query(Subscription).first()
    sub.current_period_end = datetime.utcnow() + timedelta(days=15)
    db.commit()
    service = SubscriptionService(db, NoopMercadoPago(), tenant="practiq")
    try:
        entitlement = service.get_entitlement("teacher-1")
        assert entitlement is not None, "a paused subscription keeps what it paid for"
        assert entitlement.status == "paused", "and must still read as paused"
    finally:
        db.close()


def test_paid_time_survives_a_cancellation_and_then_runs_out():
    db = _subscription_db("cancelled")
    sub = db.query(Subscription).first()
    sub.current_period_end = datetime.utcnow() + timedelta(days=3)
    db.commit()
    service = SubscriptionService(db, NoopMercadoPago(), tenant="practiq")
    try:
        assert service.get_entitlement("teacher-1") is not None

        sub.current_period_end = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        assert service.get_entitlement("teacher-1") is None, "and stops the moment it expires"
    finally:
        db.close()


def test_a_cancelled_subscription_without_a_paid_period_grants_nothing():
    """No end date on a cancelled row means nothing is known to be paid for."""
    db = _subscription_db("cancelled")
    service = SubscriptionService(db, NoopMercadoPago(), tenant="practiq")
    try:
        assert service.get_entitlement("teacher-1") is None
    finally:
        db.close()


def test_proration_charges_only_the_unused_difference():
    start = datetime(2026, 9, 24)
    end = datetime(2026, 10, 24)
    # Halfway through: 15 of 30 days left on a 100 to 200 move.
    halfway = datetime(2026, 10, 9)
    owed = SubscriptionService.proration(Decimal("100"), Decimal("200"), start, end, halfway)
    assert owed == Decimal("50.00"), owed


def test_moving_down_owes_nothing():
    start, end = datetime(2026, 9, 24), datetime(2026, 10, 24)
    owed = SubscriptionService.proration(
        Decimal("200"), Decimal("100"), start, end, datetime(2026, 10, 9)
    )
    assert owed == Decimal("0.00"), owed


def test_proration_of_an_expired_period_owes_nothing():
    start, end = datetime(2026, 9, 24), datetime(2026, 10, 24)
    owed = SubscriptionService.proration(
        Decimal("100"), Decimal("200"), start, end, datetime(2026, 10, 25)
    )
    assert owed == Decimal("0.00"), owed


def test_cancelling_ends_the_agreement_now_and_keeps_the_paid_month():
    """No deferred cancellation to go wrong: the entitlement carries the month.

    The old at_period_end mode left the agreement running and a scheduled job
    to end it. Nothing ran that job, and a late run would have charged someone
    who had already cancelled.
    """
    db = _subscription_db("authorized")
    sub = db.query(Subscription).first()
    sub.current_period_end = datetime.utcnow() + timedelta(days=12)
    db.commit()
    mp = ResumableMercadoPago()
    service = SubscriptionService(db, mp, tenant="practiq")
    try:
        cancelled = service.cancel_subscription(sub.id)
        assert cancelled.status == "cancelled"
        assert mp.cancelled == ["preapproval-1"], "the gateway stops charging immediately"
        assert service.get_entitlement("teacher-1") is not None, "the paid month is kept"
    finally:
        db.close()


class PlanChangeMercadoPago(ResumableMercadoPago):
    def __init__(self):
        super().__init__()
        self.amounts: list[tuple[str, float]] = []

    def update_subscription_amount(self, preapproval_id: str, amount: float, currency: str = "ARS") -> dict:
        self.amounts.append((preapproval_id, amount))
        return {"id": preapproval_id, "status": "authorized"}


def _paying_db():
    db = _two_plan_db()
    sub = db.query(Subscription).filter(Subscription.user_id == "teacher-1").first()
    sub.current_period_start = datetime.utcnow() - timedelta(days=15)
    sub.current_period_end = datetime.utcnow() + timedelta(days=15)
    db.commit()
    return db


def test_changing_plan_without_a_card_charges_nothing_now():
    """Paying from a Mercado Pago balance leaves nothing to charge with.

    The move still happens and the agreement is restated, so the new price
    arrives at renewal. Refusing instead would strand every teacher who pays
    from their balance on the plan they started with.
    """
    db = _paying_db()
    mp = PlanChangeMercadoPago()
    service = SubscriptionService(db, mp, webhook_url="https://hook", tenant="practiq")
    target = db.query(Plan).order_by(Plan.id.desc()).first()
    try:
        sub, charged = service.change_plan(
            PlanChangeCreate(plan_id=target.id, user_id="teacher-1", payer_email="t@example.com")
        )
        assert charged == Decimal("0.00"), charged
        assert sub.plan_id == target.id, "the plan still moves"
        assert mp.amounts == [("preapproval-old", 25000.0)], mp.amounts
        assert mp.cancelled == [], "the agreement is restated, never replaced"
    finally:
        db.close()


def test_changing_plan_keeps_one_agreement():
    """Cancelling and recreating is what charged a whole new month."""
    db = _paying_db()
    mp = PlanChangeMercadoPago()
    service = SubscriptionService(db, mp, webhook_url="https://hook", tenant="practiq")
    target = db.query(Plan).order_by(Plan.id.desc()).first()
    try:
        before = db.query(Subscription).filter(Subscription.user_id == "teacher-1").count()
        service.change_plan(
            PlanChangeCreate(plan_id=target.id, user_id="teacher-1", payer_email="t@example.com")
        )
        after = db.query(Subscription).filter(Subscription.user_id == "teacher-1").count()
        assert after == before, "no second subscription row, and no second charge"
    finally:
        db.close()
