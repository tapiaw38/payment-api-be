from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from db.models import BillingCycle, Plan, Subscription
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from schemas.subscriptions import BillingCycleCreate, PlanCreate, PlanUpdate, SubscriptionCreate


class SubscriptionService:
    def __init__(
        self,
        db: Session,
        mp_subscription: MercadopagoSubscriptionService,
        webhook_url: str = "",
        back_url: str = "",
        tenant: str = "default",
    ):
        self.db = db
        self.mp = mp_subscription
        self.webhook_url = webhook_url
        self.back_url = back_url
        # Every read a caller can reach goes through this. It comes from the
        # API key, so a product can only ever see its own rows even when it
        # asks for an id that belongs to another one.
        self.tenant = tenant

    @staticmethod
    def _as_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

    def _interval_to_frequency(self, interval: str, interval_count: int) -> tuple[int, str]:
        if interval == "month":
            return interval_count, "months"
        if interval == "year":
            return interval_count, "years"
        if interval == "day":
            return interval_count, "days"
        return 1, "months"

    def create_plan(self, data: PlanCreate) -> Plan:
        if not self.back_url:
            raise ValueError("back_url_not_configured")
        plan = Plan(
            tenant=self.tenant,
            name=data.name,
            description=data.description,
            amount=data.amount,
            currency=data.currency,
            interval=data.interval,
            interval_count=data.interval_count,
            gateway="mercadopago",
            plan_metadata=data.metadata or {},
        )
        self.db.add(plan)
        self.db.flush()
        try:
            freq, freq_type = self._interval_to_frequency(data.interval, data.interval_count)
            result = self.mp.create_plan(
                reason=data.name,
                amount=data.amount,
                back_url=self.back_url,
                currency=data.currency,
                frequency=freq,
                frequency_type=freq_type,
            )
            plan.gateway_plan_id = result.get("id")
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def list_plans(self, active_only: bool = True) -> list[Plan]:
        q = self.db.query(Plan).filter(Plan.tenant == self.tenant)
        if active_only:
            q = q.filter(Plan.active == 1)
        return q.order_by(Plan.id).all()

    def get_plan(self, plan_id: int) -> Plan | None:
        return (
            self.db.query(Plan)
            .filter(Plan.id == plan_id, Plan.tenant == self.tenant)
            .first()
        )

    def create_subscription(self, data: SubscriptionCreate) -> Subscription:
        plan = self.get_plan(data.plan_id)
        if not plan:
            raise ValueError("plan_not_found")
        if not plan.gateway_plan_id:
            raise ValueError("plan_not_linked_to_gateway")
        if not self.webhook_url:
            raise ValueError("subscription_webhook_url_not_configured")
        sub = Subscription(
            tenant=self.tenant,
            plan_id=plan.id,
            user_id=data.user_id,
            gateway="mercadopago",
            status="pending",
        )
        self.db.add(sub)
        self.db.flush()
        try:
            result = self.mp.create_subscription(
                preapproval_plan_id=plan.gateway_plan_id,
                reason=plan.name,
                payer_email=data.payer_email,
                card_token_id=data.card_token_id,
                external_reference=str(sub.id),
                notification_url=self.webhook_url,
            )
            sub.gateway_subscription_id = result.get("id")
            sub.status = result.get("status", "pending")
            self._sync_subscription_dates(sub, result)
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def _sync_subscription_dates(self, sub: Subscription, provider_data: dict) -> None:
        """Provider dates are authoritative; do not invent 30-day months locally."""
        approved_at = self._as_datetime(provider_data.get("date_approved"))
        next_payment_at = self._as_datetime(provider_data.get("next_payment_date"))
        if approved_at:
            sub.current_period_start = approved_at
        if next_payment_at:
            sub.next_payment_at = next_payment_at
            sub.current_period_end = next_payment_at

    def get_subscription(self, subscription_id: int) -> Subscription | None:
        return (
            self.db.query(Subscription)
            .filter(Subscription.id == subscription_id, Subscription.tenant == self.tenant)
            .first()
        )

    def get_subscription_by_user(self, user_id: str) -> list[Subscription]:
        return (
            self.db.query(Subscription)
            .filter(Subscription.user_id == user_id, Subscription.tenant == self.tenant)
            .order_by(Subscription.created_at.desc())
            .all()
        )

    def get_entitlement(self, user_id: str) -> Subscription | None:
        now = datetime.utcnow()
        return (
            self.db.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.tenant == self.tenant,
                Subscription.status.in_(["authorized", "active"]),
                (Subscription.current_period_end.is_(None)) | (Subscription.current_period_end > now),
            )
            .order_by(Subscription.current_period_end.desc().nullslast(), Subscription.created_at.desc())
            .first()
        )

    def update_plan(self, plan_id: int, data: PlanUpdate) -> Plan | None:
        """Edits a plan.

        Only what new subscribers will be charged. Everyone already subscribed
        holds their own agreement at the amount they accepted, and moving them
        is a separate, deliberate act — a price rise that applied itself to
        existing payers without asking is a chargeback, not a feature.
        """
        plan = self.get_plan(plan_id)
        if not plan:
            return None

        if plan.gateway_plan_id and (data.name is not None or data.amount is not None):
            try:
                self.mp.update_plan(
                    preapproval_plan_id=plan.gateway_plan_id,
                    reason=data.name,
                    amount=data.amount,
                    currency=data.currency or plan.currency,
                )
            except MercadopagoAPIException:
                self.db.rollback()
                raise

        for field in ("name", "description", "amount", "currency"):
            value = getattr(data, field)
            if value is not None:
                setattr(plan, field, value)
        if data.metadata is not None:
            plan.plan_metadata = data.metadata
        if data.active is not None:
            plan.active = 1 if data.active else 0

        self.db.commit()
        self.db.refresh(plan)
        return plan

    def deactivate_plan(self, plan_id: int) -> Plan | None:
        """Takes a plan off the shelf without deleting it.

        Subscriptions point at their plan, and people keep paying for what they
        bought after it stops being sold. Deleting the row would leave those
        subscriptions describing a plan that no longer exists.
        """
        plan = self.get_plan(plan_id)
        if not plan:
            return None
        plan.active = 0
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def pause_subscription(self, subscription_id: int) -> Subscription | None:
        sub = self.get_subscription(subscription_id)
        if not sub or not sub.gateway_subscription_id:
            return None
        try:
            self.mp.pause_subscription(sub.gateway_subscription_id)
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        sub.status = "paused"
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def resume_subscription(self, subscription_id: int) -> Subscription | None:
        """Puts a paused subscription back to work.

        Only paused ones. A cancelled agreement cannot be revived at the
        gateway: the payer authorised a charge and then withdrew it, and the
        only way back is for them to authorise a new one.
        """
        sub = self.get_subscription(subscription_id)
        if not sub or not sub.gateway_subscription_id:
            return None
        if sub.status != "paused":
            raise ValueError("subscription_not_paused")
        try:
            self.mp.resume_subscription(sub.gateway_subscription_id)
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        sub.status = "authorized"
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def cancel_subscription(self, subscription_id: int, at_period_end: bool = False) -> Subscription | None:
        sub = self.get_subscription(subscription_id)
        if not sub or not sub.gateway_subscription_id:
            return None
        if at_period_end:
            sub.cancel_at_period_end = 1
            self.db.commit()
            self.db.refresh(sub)
            return sub
        try:
            self.mp.cancel_subscription(sub.gateway_subscription_id)
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        sub.status = "cancelled"
        sub.cancelled_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def cancel_due_subscriptions(self, now: datetime | None = None) -> list[Subscription]:
        """Called by a trusted scheduled worker after paid access expires."""
        now = now or datetime.utcnow()
        due = (
            self.db.query(Subscription)
            .filter(
                Subscription.cancel_at_period_end == 1,
                Subscription.current_period_end.isnot(None),
                Subscription.current_period_end <= now,
                Subscription.status.in_(["authorized", "active"]),
            )
            .all()
        )
        cancelled = []
        for sub in due:
            try:
                self.mp.cancel_subscription(sub.gateway_subscription_id)
            except MercadopagoAPIException:
                self.db.rollback()
                continue
            sub.status = "cancelled"
            sub.cancelled_at = now
            cancelled.append(sub)
        self.db.commit()
        return cancelled

    # The methods below are reached from webhooks, which have no API key and so
    # no tenant. They look rows up by gateway identifiers, which the gateway
    # issues globally, so a lookup cannot land on another product's row and
    # there is nothing for a tenant filter to narrow.
    def update_subscription_status(self, gateway_subscription_id: str, status: str) -> Subscription | None:
        sub = (
            self.db.query(Subscription)
            .filter(Subscription.gateway_subscription_id == gateway_subscription_id)
            .first()
        )
        if not sub:
            return None
        sub.status = status
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def sync_subscription_from_gateway(self, provider_data: dict) -> Subscription | None:
        sub = (
            self.db.query(Subscription)
            .filter(Subscription.gateway_subscription_id == provider_data.get("id"))
            .first()
        )
        if not sub:
            return None
        sub.status = provider_data.get("status", sub.status)
        self._sync_subscription_dates(sub, provider_data)
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def create_billing_cycle(self, subscription_id: int, data: BillingCycleCreate) -> BillingCycle:
        sub = self.get_subscription(subscription_id)
        if not sub:
            raise ValueError("subscription_not_found")
        if data.quantity < 0 or data.unit_amount < 0 or data.minimum_amount < 0:
            raise ValueError("invalid_billing_amount")
        if data.period_end <= data.period_start:
            raise ValueError("invalid_billing_period")
        existing = (
            self.db.query(BillingCycle)
            .filter(BillingCycle.subscription_id == subscription_id, BillingCycle.period_start == data.period_start)
            .first()
        )
        if existing:
            raise ValueError("billing_cycle_already_exists")
        amount = max(data.unit_amount * data.quantity, data.minimum_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        cycle = BillingCycle(
            subscription_id=subscription_id,
            period_start=data.period_start,
            period_end=data.period_end,
            quantity=data.quantity,
            unit_amount=data.unit_amount,
            minimum_amount=data.minimum_amount,
            amount=amount,
            currency=sub.plan.currency,
        )
        self.db.add(cycle)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    def update_next_charge_amount(self, cycle_id: int) -> BillingCycle | None:
        cycle = self.db.query(BillingCycle).filter(BillingCycle.id == cycle_id).first()
        if not cycle or not cycle.subscription.gateway_subscription_id:
            return None
        self.mp.update_subscription_amount(
            cycle.subscription.gateway_subscription_id,
            float(cycle.amount),
            cycle.currency,
        )
        return cycle

    def sync_authorized_payment(self, provider_data: dict) -> BillingCycle | None:
        subscription_id = provider_data.get("preapproval_id")
        if not subscription_id:
            return None
        sub = (
            self.db.query(Subscription)
            .filter(Subscription.gateway_subscription_id == str(subscription_id))
            .first()
        )
        if not sub:
            return None
        cycle = (
            self.db.query(BillingCycle)
            .filter(BillingCycle.subscription_id == sub.id, BillingCycle.status == "pending")
            .order_by(BillingCycle.period_start.asc())
            .first()
        )
        if not cycle:
            return None
        cycle.gateway_authorized_payment_id = str(provider_data.get("id"))
        cycle.status = provider_data.get("status", cycle.status)
        payment = provider_data.get("payment") or {}
        if isinstance(payment, dict) and payment.get("id"):
            cycle.gateway_payment_id = str(payment["id"])
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    def sync_recurring_payment(self, provider_data: dict) -> BillingCycle | None:
        """Attach the real charge to its cycle when Mercado Pago emits payment."""
        preapproval_id = provider_data.get("preapproval_id")
        if not preapproval_id:
            return None
        sub = (
            self.db.query(Subscription)
            .filter(Subscription.gateway_subscription_id == str(preapproval_id))
            .first()
        )
        if not sub:
            return None
        cycle = (
            self.db.query(BillingCycle)
            .filter(BillingCycle.subscription_id == sub.id, BillingCycle.status.in_(["pending", "scheduled"]))
            .order_by(BillingCycle.period_start.asc())
            .first()
        )
        if not cycle:
            return None
        cycle.gateway_payment_id = str(provider_data.get("id"))
        cycle.status = provider_data.get("status", cycle.status)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle
