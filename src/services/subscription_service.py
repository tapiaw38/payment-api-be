from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from db.models import Plan, Subscription
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from schemas.subscriptions import PlanCreate, SubscriptionCreate


class SubscriptionService:
    def __init__(
        self,
        db: Session,
        mp_subscription: MercadopagoSubscriptionService,
    ):
        self.db = db
        self.mp = mp_subscription

    def _interval_to_frequency(self, interval: str, interval_count: int) -> tuple[int, str]:
        if interval == "month":
            return interval_count, "months"
        if interval == "year":
            return interval_count, "years"
        if interval == "day":
            return interval_count, "days"
        return 1, "months"

    def create_plan(self, data: PlanCreate) -> Plan:
        plan = Plan(
            name=data.name,
            description=data.description,
            amount=data.amount,
            currency=data.currency,
            interval=data.interval,
            interval_count=data.interval_count,
            gateway="mercadopago",
        )
        self.db.add(plan)
        self.db.flush()
        try:
            freq, freq_type = self._interval_to_frequency(data.interval, data.interval_count)
            result = self.mp.create_plan(
                reason=data.name,
                amount=data.amount,
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
        q = self.db.query(Plan)
        if active_only:
            q = q.filter(Plan.active == 1)
        return q.order_by(Plan.id).all()

    def get_plan(self, plan_id: int) -> Plan | None:
        return self.db.query(Plan).filter(Plan.id == plan_id).first()

    def create_subscription(self, data: SubscriptionCreate) -> Subscription:
        plan = self.get_plan(data.plan_id)
        if not plan:
            raise ValueError("plan_not_found")
        if not plan.gateway_plan_id:
            raise ValueError("plan_not_linked_to_gateway")
        sub = Subscription(
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
                notification_url=data.notification_url,
            )
            sub.gateway_subscription_id = result.get("id")
            sub.status = result.get("status", "pending")
            if result.get("date_approved"):
                sub.current_period_start = datetime.utcnow()
                if plan.interval == "month":
                    sub.current_period_end = sub.current_period_start + timedelta(days=30 * plan.interval_count)
                elif plan.interval == "year":
                    sub.current_period_end = sub.current_period_start + timedelta(days=365 * plan.interval_count)
                else:
                    sub.current_period_end = sub.current_period_start + timedelta(days=plan.interval_count)
        except MercadopagoAPIException:
            self.db.rollback()
            raise
        self.db.commit()
        self.db.refresh(sub)
        return sub

    def get_subscription(self, subscription_id: int) -> Subscription | None:
        return self.db.query(Subscription).filter(Subscription.id == subscription_id).first()

    def get_subscription_by_user(self, user_id: str) -> list[Subscription]:
        return (
            self.db.query(Subscription)
            .filter(Subscription.user_id == user_id)
            .order_by(Subscription.created_at.desc())
            .all()
        )

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
