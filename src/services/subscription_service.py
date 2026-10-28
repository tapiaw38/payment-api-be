from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import BillingCycle, Payment, Plan, PlanChangeAttempt, Subscription
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.payment_models import PaymentCreate as GatewayPaymentCreate, PaymentPayer as GatewayPaymentPayer
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from schemas.subscriptions import (
    BillingCycleCreate,
    HostedSubscriptionCreate,
    PlanChangeCreate,
    PlanCreate,
    PlanUpdate,
    SubscriptionCreate,
)


class PlanChangeOutcomeUnknown(Exception):
    pass


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

    # Statuses that mean the gateway may still charge this subscription. A
    # cancelled one never will, and a rejected one never did.
    LIVE_STATUSES = ("pending", "authorized", "active", "paused")

    def live_subscription(self, user_id: str) -> Subscription | None:
        """The subscription a user is currently on the hook for, if any."""
        return (
            self.db.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.tenant == self.tenant,
                Subscription.status.in_(self.LIVE_STATUSES),
            )
            .order_by(Subscription.created_at.desc())
            .first()
        )

    def list_subscriptions_admin(self, status: str | None = None) -> list[dict]:
        query = self.db.query(Subscription).filter(Subscription.tenant == self.tenant)
        if status:
            query = query.filter(Subscription.status == status)
        rows = []
        for sub in query.order_by(Subscription.created_at.desc()).all():
            last_cycle = (
                self.db.query(BillingCycle)
                .filter(BillingCycle.subscription_id == sub.id)
                .order_by(BillingCycle.period_start.desc())
                .first()
            )
            rows.append(
                {
                    "id": sub.id,
                    "user_id": sub.user_id,
                    "payer_email": sub.payer_email,
                    "plan_id": sub.plan_id,
                    "plan_name": sub.plan.name,
                    "status": sub.status,
                    "current_period_end": sub.current_period_end,
                    "next_payment_at": sub.next_payment_at,
                    "created_at": sub.created_at,
                    "last_cycle": None
                    if not last_cycle
                    else {
                        "status": last_cycle.status,
                        "amount": last_cycle.amount,
                        "currency": last_cycle.currency,
                        "period_start": last_cycle.period_start,
                        "period_end": last_cycle.period_end,
                        "gateway_payment_id": last_cycle.gateway_payment_id,
                    },
                }
            )
        return rows

    def create_subscription(self, data: SubscriptionCreate) -> Subscription:
        plan = self.get_plan(data.plan_id)
        if not plan:
            raise ValueError("plan_not_found")
        if not plan.gateway_plan_id:
            raise ValueError("plan_not_linked_to_gateway")
        if not self.webhook_url:
            raise ValueError("subscription_webhook_url_not_configured")

        # Everything that can be checked is checked before anything is
        # cancelled, so a request that was never going to work does not cost
        # somebody the subscription they already had.
        # An agreement opened by hosted checkout and never authorised counts as
        # live, so without this a teacher who bounced off the gateway could not
        # pay by card either — locked out of both doors by a row that grants
        # nothing.
        self._discard_pending(data.user_id)

        cancelled_at_gateway: str | None = None
        existing = self.live_subscription(data.user_id)
        if existing:
            if existing.plan_id == plan.id:
                raise ValueError("already_subscribed_to_plan")
            # Cancelled first, deliberately. Creating first and cancelling
            # after leaves two live agreements at the gateway if the second
            # step fails, and the payer is charged twice; failing this way
            # leaves them on the free plan, able to try again. Losing access
            # is recoverable, a double charge is a refund and a lost customer.
            cancelled_at_gateway = existing.gateway_subscription_id
            if cancelled_at_gateway:
                self.mp.cancel_subscription(cancelled_at_gateway)
            existing.status = "cancelled"
            existing.cancelled_at = datetime.utcnow()
            self.db.flush()

        sub = Subscription(
            tenant=self.tenant,
            plan_id=plan.id,
            user_id=data.user_id,
            gateway="mercadopago",
            status="pending",
            payer_email=data.payer_email,
        )
        self.db.add(sub)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            if cancelled_at_gateway:
                self._record_cancellation(cancelled_at_gateway)
            raise ValueError("already_subscribed_to_plan")
        try:
            result = self.mp.create_subscription(
                preapproval_plan_id=plan.gateway_plan_id,
                reason=plan.name,
                payer_email=data.payer_email,
                card_token_id=data.card_token_id,
                external_reference=str(sub.id),
                notification_url=self.webhook_url,
                idempotency_key=f"sub-create:{sub.id}",
            )
            sub.gateway_subscription_id = result.get("id")
            sub.status = result.get("status", "pending")
            self._sync_subscription_dates(sub, result)
        except MercadopagoAPIException:
            self.db.rollback()
            # A rollback undoes our rows, not the gateway's. The old agreement
            # was already cancelled there and no rollback brings it back, so
            # the record has to be corrected to say so — otherwise we would
            # show a subscription as live that will never be charged again.
            if cancelled_at_gateway:
                self._record_cancellation(cancelled_at_gateway)
            raise
        self.db.commit()
        self.db.refresh(sub)
        return sub

    @staticmethod
    def proration(
        old_amount: Decimal,
        new_amount: Decimal,
        period_start: datetime | None,
        period_end: datetime | None,
        now: datetime,
    ) -> Decimal:
        """What the rest of this period costs at the difference in price.

        Moving up mid-cycle used to mean paying the new plan in full on top of
        the month already bought, and forfeiting what was left of it. Only the
        unused part of the gap is owed. Moving down owes nothing: the cheaper
        price starts at renewal and nothing is refunded.
        """
        gap = Decimal(new_amount) - Decimal(old_amount)
        if gap <= 0 or not period_end or period_end <= now:
            return Decimal("0.00")
        # A period with no recorded start is a month, which is what every plan
        # here bills. Guessing longer would undercharge.
        start = period_start or (period_end - timedelta(days=30))
        total = (period_end - start).total_seconds()
        if total <= 0:
            return Decimal("0.00")
        remaining = (period_end - now).total_seconds()
        ratio = Decimal(str(min(remaining / total, 1.0)))
        return (gap * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def change_plan(self, data: PlanChangeCreate) -> tuple[Subscription, Decimal]:
        """Moves a live subscription to another plan, keeping the agreement.

        Cancelling and recreating charged the new plan in full immediately and
        threw away the rest of the month already paid for — 300 for one month
        on a 100 plan moving to 200 halfway. The gateway can restate the amount
        on the agreement instead, which leaves the billing date alone, so the
        only thing owed now is the difference for the days remaining.
        """
        plan = self.get_plan(data.plan_id)
        if not plan:
            raise ValueError("plan_not_found")
        sub = self.live_subscription(data.user_id)
        if not sub or not sub.gateway_subscription_id:
            raise ValueError("no_live_subscription")
        attempt = self._open_plan_change_attempt(sub.id)
        if attempt:
            if attempt.target_plan_id != plan.id:
                raise PlanChangeOutcomeUnknown("plan_change_in_progress")
            if attempt.state == "prepared":
                if not (data.card_token_id and data.payment_method_id):
                    raise PlanChangeOutcomeUnknown("plan_change_in_progress")
                return self._execute_plan_change(sub, data, attempt, plan.name)
            return self._reconcile_plan_change(sub, attempt)
        if sub.status not in ("authorized", "active"):
            # A paused agreement is not being charged; resuming it first is
            # what the teacher means, and it keeps the billing date honest.
            raise ValueError("subscription_not_active")
        if sub.plan_id == plan.id:
            completed = (
                self.db.query(PlanChangeAttempt)
                .filter(
                    PlanChangeAttempt.tenant == self.tenant,
                    PlanChangeAttempt.subscription_id == sub.id,
                    PlanChangeAttempt.target_plan_id == plan.id,
                    PlanChangeAttempt.state == "complete",
                )
                .order_by(PlanChangeAttempt.id.desc())
                .first()
            )
            if completed:
                return sub, Decimal(completed.amount)
            raise ValueError("already_subscribed_to_plan")

        old_amount = Decimal(sub.plan.amount) if sub.plan else Decimal("0")
        new_amount = Decimal(plan.amount)
        currency = plan.currency or "ARS"
        owed = self.proration(
            old_amount, new_amount, sub.current_period_start, sub.current_period_end, datetime.utcnow()
        )

        if owed > 0 and not (data.card_token_id and data.payment_method_id):
            # Paying from a balance leaves us nothing to charge the difference
            # with. The move still happens; the new price starts at renewal.
            owed = Decimal("0.00")

        if owed <= 0:
            self.mp.update_subscription_amount(sub.gateway_subscription_id, float(new_amount), currency)
            sub.plan_id = plan.id
            self.db.commit()
            self.db.refresh(sub)
            return sub, owed

        attempt = PlanChangeAttempt(
            tenant=self.tenant,
            subscription_id=sub.id,
            source_plan_id=sub.plan_id,
            target_plan_id=plan.id,
            period_start=sub.current_period_start,
            period_end=sub.current_period_end,
            amount=owed,
            target_amount=new_amount,
            currency=currency,
            state="prepared",
        )
        self.db.add(attempt)
        try:
            self.db.flush()
            attempt.idempotency_key = f"plan-change:{attempt.id}"
            attempt.external_reference = f"plan-change-attempt-{attempt.id}"
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise PlanChangeOutcomeUnknown("plan_change_in_progress")

        return self._execute_plan_change(sub, data, attempt, plan.name)

    def _execute_plan_change(
        self, sub: Subscription, data: PlanChangeCreate, attempt: PlanChangeAttempt, plan_name: str
    ) -> tuple[Subscription, Decimal]:
        try:
            self.mp.update_subscription_amount(
                sub.gateway_subscription_id, float(attempt.target_amount), attempt.currency
            )
        except Exception:
            attempt.state = "failed"
            self.db.commit()
            raise

        attempt.state = "sending"
        self.db.commit()
        try:
            result = self._charge_difference(data, attempt, plan_name)
        except MercadopagoAPIException as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409, 423, 429):
                attempt.state = "unknown"
                self.db.commit()
                raise PlanChangeOutcomeUnknown("payment_outcome_unknown") from exc
            attempt.state = "failed"
            self.db.commit()
            self.mp.update_subscription_amount(
                sub.gateway_subscription_id, float(sub.plan.amount), attempt.currency
            )
            raise
        except requests.RequestException as exc:
            attempt.state = "unknown"
            self.db.commit()
            raise PlanChangeOutcomeUnknown("payment_outcome_unknown") from exc

        if not self._payment_matches_attempt(attempt, result):
            attempt.state = "unknown"
            self.db.commit()
            raise PlanChangeOutcomeUnknown("payment_outcome_unknown")
        self._record_plan_change_result(attempt, result)
        if attempt.state == "failed":
            self.mp.update_subscription_amount(
                sub.gateway_subscription_id, float(sub.plan.amount), attempt.currency
            )
            raise ValueError("payment_rejected")
        if attempt.state != "charged":
            raise PlanChangeOutcomeUnknown("payment_outcome_unknown")

        return self._finish_plan_change(sub, attempt)

    def _open_plan_change_attempt(self, subscription_id: int) -> PlanChangeAttempt | None:
        return (
            self.db.query(PlanChangeAttempt)
            .filter(
                PlanChangeAttempt.tenant == self.tenant,
                PlanChangeAttempt.subscription_id == subscription_id,
                PlanChangeAttempt.state.in_(("prepared", "sending", "unknown", "charged")),
            )
            .first()
        )

    def _record_plan_change_result(self, attempt: PlanChangeAttempt, result: dict) -> None:
        attempt.gateway_payment_id = str(result.get("id")) if result.get("id") else None
        attempt.gateway_status = result.get("status")
        if attempt.gateway_status in ("approved", "accredited"):
            attempt.state = "charged"
        elif attempt.gateway_status in ("rejected", "cancelled"):
            attempt.state = "failed"
        else:
            attempt.state = "unknown"
        self.db.commit()

    @staticmethod
    def _payment_matches_attempt(attempt: PlanChangeAttempt, result: dict) -> bool:
        try:
            amount = Decimal(str(result["transaction_amount"])).quantize(Decimal("0.01"))
        except (KeyError, ValueError, TypeError):
            return False
        return (
            bool(result.get("id"))
            and amount == Decimal(attempt.amount)
            and result.get("currency_id") == attempt.currency
            and result.get("external_reference") == attempt.external_reference
        )

    def _reconcile_plan_change(
        self, sub: Subscription, attempt: PlanChangeAttempt
    ) -> tuple[Subscription, Decimal]:
        if attempt.state == "charged":
            return self._finish_plan_change(sub, attempt)
        gateway = MercadopagoPaymentService(access_token=self.mp.access_token)
        try:
            payments = gateway.find_payments_by_reference(attempt.external_reference)
        except (MercadopagoAPIException, requests.RequestException) as exc:
            raise PlanChangeOutcomeUnknown("payment_outcome_unknown") from exc
        if len(payments) != 1:
            attempt.state = "unknown"
            self.db.commit()
            raise PlanChangeOutcomeUnknown("payment_outcome_unknown")
        result = payments[0]
        if not self._payment_matches_attempt(attempt, result):
            raise PlanChangeOutcomeUnknown("payment_outcome_unknown")
        self._record_plan_change_result(attempt, result)
        if attempt.state == "charged":
            return self._finish_plan_change(sub, attempt)
        if attempt.state == "failed":
            self.mp.update_subscription_amount(sub.gateway_subscription_id, float(sub.plan.amount), attempt.currency)
            raise ValueError("payment_rejected")
        raise PlanChangeOutcomeUnknown("payment_outcome_unknown")

    def _finish_plan_change(
        self, sub: Subscription, attempt: PlanChangeAttempt
    ) -> tuple[Subscription, Decimal]:
        attempt = (
            self.db.query(PlanChangeAttempt)
            .filter(PlanChangeAttempt.id == attempt.id, PlanChangeAttempt.tenant == self.tenant)
            .populate_existing()
            .with_for_update()
            .one()
        )
        if attempt.state == "complete":
            return sub, Decimal(attempt.amount)
        if attempt.state != "charged":
            raise PlanChangeOutcomeUnknown("payment_outcome_unknown")
        self.mp.update_subscription_amount(
            sub.gateway_subscription_id, float(attempt.target_amount), attempt.currency
        )
        payment = (
            self.db.query(Payment)
            .filter(Payment.tenant == self.tenant, Payment.external_reference == attempt.external_reference)
            .first()
        )
        if not payment:
            self.db.add(
                Payment(
                    tenant=self.tenant,
                    gateway="mercadopago",
                    gateway_payment_id=attempt.gateway_payment_id,
                    amount=attempt.amount,
                    currency=attempt.currency,
                    status=attempt.gateway_status,
                    user_id=sub.user_id,
                    external_reference=attempt.external_reference,
                    description="Diferencia por cambio de plan",
                )
            )
        sub.plan_id = attempt.target_plan_id
        attempt.state = "complete"
        self.db.commit()
        self.db.refresh(sub)
        return sub, Decimal(attempt.amount)

    def _charge_difference(
        self,
        data: PlanChangeCreate,
        attempt: PlanChangeAttempt,
        plan_name: str,
    ) -> dict:
        gateway = MercadopagoPaymentService(access_token=self.mp.access_token)
        return gateway.create_payment(
            GatewayPaymentCreate(
                transaction_amount=float(attempt.amount),
                token=data.card_token_id,
                payment_method_id=data.payment_method_id,
                payer=GatewayPaymentPayer(email=data.payer_email),
                description=f"Diferencia por pasar a {plan_name}",
                external_reference=attempt.external_reference,
            ),
            idempotency_key=attempt.idempotency_key,
        )

    def start_hosted_subscription(self, data: HostedSubscriptionCreate) -> tuple[Subscription, str]:
        """Opens an agreement for the payer to authorise at Mercado Pago.

        Refused for somebody who already has a live subscription. The card flow
        can cancel the old agreement first because it learns immediately
        whether the new one took; here the payer may simply close the tab, and
        cancelling up front would leave them paying for nothing. Changing plan
        keeps going through the card.
        """
        plan = self.get_plan(data.plan_id)
        if not plan:
            raise ValueError("plan_not_found")
        if not self.webhook_url:
            raise ValueError("subscription_webhook_url_not_configured")
        if not self.back_url:
            raise ValueError("back_url_not_configured")

        # An agreement they opened and walked away from. Sending them back to
        # the same one is what they meant by clicking again, and it costs the
        # gateway nothing; only a different plan is worth starting over for.
        pending = self._pending_subscription(data.user_id)
        if pending:
            resumed = self._resume_pending(pending, plan.id, data.payer_email)
            if resumed:
                return resumed
            self._discard_pending(data.user_id)

        if self.live_subscription(data.user_id):
            raise ValueError("already_subscribed")

        sub = Subscription(
            tenant=self.tenant,
            plan_id=plan.id,
            user_id=data.user_id,
            gateway="mercadopago",
            status="pending",
            payer_email=data.payer_email,
        )
        self.db.add(sub)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise ValueError("already_subscribed")
        try:
            result = self.mp.create_pending_subscription(
                reason=plan.name,
                payer_email=data.payer_email,
                amount=float(plan.amount),
                currency=plan.currency or "ARS",
                back_url=self.back_url,
                external_reference=str(sub.id),
                notification_url=self.webhook_url,
                idempotency_key=f"sub-create:{sub.id}",
            )
            init_point = result.get("init_point") or ""
            if not init_point:
                # Without somewhere to send the payer the row would sit pending
                # for ever, so this counts as a failure, not a partial success.
                raise ValueError("gateway_returned_no_init_point")
            sub.gateway_subscription_id = result.get("id")
            sub.status = result.get("status", "pending")
        except (MercadopagoAPIException, ValueError):
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(sub)
        return sub, init_point

    def _resume_pending(
        self, pending: Subscription, plan_id: int, payer_email: str
    ) -> tuple[Subscription, str] | None:
        """Sends the payer back to an agreement they already opened.

        Returns None when there is nothing to go back to: a different plan, or
        a gateway that has since moved the agreement on — including the case
        where they did authorise it and only our row is behind, which the
        status is corrected for here rather than left to the webhook.
        """
        if pending.plan_id != plan_id or not pending.gateway_subscription_id:
            return None
        # Asking again with a different address is how somebody corrects the
        # one the gateway rejected. Sending them back would repeat the refusal.
        if (pending.payer_email or "") != payer_email:
            return None
        try:
            result = self.mp.get_subscription(pending.gateway_subscription_id)
        except MercadopagoAPIException:
            return None
        status = result.get("status", "")
        if status != "pending":
            pending.status = status or pending.status
            self._sync_subscription_dates(pending, result)
            self.db.commit()
            return None
        init_point = result.get("init_point") or ""
        return (pending, init_point) if init_point else None

    def _pending_subscription(self, user_id: str) -> Subscription | None:
        """An agreement opened but never authorised by the payer.

        Hosted checkout leaves one behind whenever somebody closes the tab at
        the gateway, which is most of the time. It is not a subscription — it
        grants nothing — so it must never stand between them and paying.
        """
        return (
            self.db.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.tenant == self.tenant,
                Subscription.status == "pending",
            )
            .order_by(Subscription.created_at.desc())
            .first()
        )

    def _discard_pending(self, user_id: str) -> None:
        """Clears abandoned agreements so a payer can try again."""
        pending = self._pending_subscription(user_id)
        while pending:
            if pending.gateway_subscription_id:
                try:
                    self.mp.cancel_subscription(pending.gateway_subscription_id)
                except MercadopagoAPIException:
                    # Already gone at the gateway, or unreachable. Either way an
                    # agreement nobody authorised must not block this payer.
                    pass
            pending.status = "cancelled"
            pending.cancelled_at = datetime.utcnow()
            self.db.flush()
            pending = self._pending_subscription(user_id)

    def _record_cancellation(self, gateway_subscription_id: str) -> None:
        stale = (
            self.db.query(Subscription)
            .filter(Subscription.gateway_subscription_id == gateway_subscription_id)
            .first()
        )
        if not stale:
            return
        stale.status = "cancelled"
        stale.cancelled_at = datetime.utcnow()
        self.db.commit()

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

    def list_billing_cycles_for_user(self, user_id: str, limit: int = 24) -> list[BillingCycle]:
        return (
            self.db.query(BillingCycle)
            .join(Subscription)
            .filter(Subscription.user_id == user_id, Subscription.tenant == self.tenant)
            .order_by(BillingCycle.period_start.desc(), BillingCycle.id.desc())
            .limit(limit)
            .all()
        )

    # Statuses that mean somebody is entitled right now. Paused and cancelled
    # are in because the period they already paid for does not stop being paid
    # for: pausing on the 9th, covered to the 24th, used to cost two weeks of
    # access that had been bought. What they stop is the next charge, not this
    # one. Pending is not in — nothing has been paid yet.
    ENTITLED_STATUSES = ("authorized", "active", "paused", "cancelled")

    def get_entitlement(self, user_id: str) -> Subscription | None:
        now = datetime.utcnow()
        return (
            self.db.query(Subscription)
            .filter(
                Subscription.user_id == user_id,
                Subscription.tenant == self.tenant,
                Subscription.status.in_(self.ENTITLED_STATUSES),
                # A live subscription with no end date is entitled; a paused or
                # cancelled one has to name the day it was paid up to.
                (
                    Subscription.current_period_end.is_(None)
                    & Subscription.status.in_(["authorized", "active"])
                )
                | (Subscription.current_period_end > now),
            )
            # NULLS LAST spelled portably: SQLite has no such clause, and the
            # tests run on it — so the untestable version was the one shipped
            # to a money path.
            .order_by(
                Subscription.current_period_end.is_(None).asc(),
                Subscription.current_period_end.desc(),
                Subscription.created_at.desc(),
            )
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

    def cancel_subscription(self, subscription_id: int) -> Subscription | None:
        """Ends the agreement now; the period already paid for is kept.

        There used to be an at_period_end mode that deferred the cancellation
        itself and left a scheduled job to carry it out. Nothing ran that job,
        and a late run would have charged somebody who had cancelled. Since the
        entitlement now honours a cancelled subscription until its period ends,
        cancelling at the gateway straight away gives the same access with
        nothing left to go wrong.
        """
        sub = self.get_subscription(subscription_id)
        if not sub or not sub.gateway_subscription_id:
            return None
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

    def _refresh_subscription_from_gateway(self, sub: Subscription) -> None:
        if not sub.gateway_subscription_id:
            return
        data = self.mp.get_subscription(sub.gateway_subscription_id)
        sub.status = data.get("status", sub.status)
        self._sync_subscription_dates(sub, data)
        self.db.commit()
        self.db.refresh(sub)

    def _open_cycle_for_charge(
        self,
        sub: Subscription,
        statuses: tuple[str, ...],
        amount: Decimal,
        currency: str,
        provider_data: dict,
        authorized_payment_id: str | None = None,
        payment_id: str | None = None,
    ) -> BillingCycle:
        if authorized_payment_id:
            cycle = (
                self.db.query(BillingCycle)
                .filter(BillingCycle.gateway_authorized_payment_id == authorized_payment_id)
                .first()
            )
            if cycle:
                return cycle
        if payment_id:
            cycle = (
                self.db.query(BillingCycle)
                .filter(
                    BillingCycle.subscription_id == sub.id,
                    BillingCycle.gateway_payment_id == payment_id,
                )
                .first()
            )
            if cycle:
                return cycle
        cycle = (
            self.db.query(BillingCycle)
            .filter(BillingCycle.subscription_id == sub.id, BillingCycle.status.in_(statuses))
            .order_by(BillingCycle.period_start.asc())
            .first()
        )
        if cycle:
            return cycle
        previous_period_end = sub.current_period_end
        self._refresh_subscription_from_gateway(sub)
        period_start = self._as_datetime(
            provider_data.get("debit_date")
            or provider_data.get("date_approved")
            or provider_data.get("date_created")
        )
        if not period_start and previous_period_end and sub.current_period_end and previous_period_end < sub.current_period_end:
            period_start = previous_period_end
        period_start = period_start or sub.current_period_start or sub.created_at
        period_end = sub.current_period_end or period_start
        if period_start >= period_end:
            raise ValueError("invalid_billing_period")
        sub.current_period_start = period_start
        cycle = BillingCycle(
            subscription_id=sub.id,
            period_start=period_start,
            period_end=period_end,
            quantity=1,
            unit_amount=amount,
            minimum_amount=Decimal("0"),
            amount=amount,
            currency=currency,
            status="pending",
        )
        self.db.add(cycle)
        self.db.commit()
        self.db.refresh(cycle)
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
        amount = Decimal(str(provider_data.get("transaction_amount", "0")))
        currency = provider_data.get("currency_id") or sub.plan.currency or "ARS"
        payment = provider_data.get("payment") or {}
        payment_id = str(payment["id"]) if isinstance(payment, dict) and payment.get("id") else None
        authorized_payment_id = str(provider_data["id"]) if provider_data.get("id") else None
        cycle = self._open_cycle_for_charge(
            sub, ("pending",), amount, currency, provider_data, authorized_payment_id, payment_id
        )
        cycle.gateway_authorized_payment_id = authorized_payment_id
        cycle.status = provider_data.get("status", cycle.status)
        if payment_id:
            cycle.gateway_payment_id = payment_id
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
        amount = Decimal(str(provider_data.get("transaction_amount", "0")))
        currency = provider_data.get("currency_id") or sub.plan.currency or "ARS"
        payment_id = str(provider_data["id"]) if provider_data.get("id") else None
        cycle = self._open_cycle_for_charge(
            sub, ("pending", "scheduled"), amount, currency, provider_data, payment_id=payment_id
        )
        cycle.gateway_payment_id = payment_id
        cycle.status = provider_data.get("status", cycle.status)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle
