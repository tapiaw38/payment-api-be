from datetime import datetime
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from .session import Base


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    # Which product these rows belong to. `user_id` is an opaque string from
    # the calling product's own auth, so two products can hand us the same one;
    # without this, a lookup by user id could answer with another product's
    # data. Resolved from the API key, never from the request body.
    tenant = Column(String(64), nullable=False, index=True, server_default="default")
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), default="ARS")
    interval = Column(String(20), nullable=False)
    interval_count = Column(Integer, default=1)
    gateway = Column(String(50), nullable=False, default="mercadopago")
    gateway_plan_id = Column(String(255), nullable=True, index=True)
    # What the plan grants, in the calling product's own words. This service
    # stores it and hands it back untouched: it has no opinion on whether
    # {"max_students": 5} means anything, and adding one would make every
    # product's rules a reason to deploy this one.
    plan_metadata = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")
    active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscriptions = relationship("Subscription", back_populates="plan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    tenant = Column(String(64), nullable=False, index=True, server_default="default")
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    user_id = Column(String(255), nullable=False, index=True)
    gateway = Column(String(50), nullable=False, default="mercadopago")
    gateway_subscription_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="pending", index=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Integer, default=0)
    cancelled_at = Column(DateTime, nullable=True)
    next_payment_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("Plan", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    tenant = Column(String(64), nullable=False, index=True, server_default="default")
    gateway = Column(String(50), nullable=False, default="mercadopago")
    gateway_payment_id = Column(String(255), nullable=True, index=True)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), default="ARS")
    status = Column(String(50), nullable=False, index=True)
    user_id = Column(String(255), nullable=True, index=True)
    external_reference = Column(String(255), nullable=True, index=True)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

    id = Column(Integer, primary_key=True, index=True)
    tenant = Column(String(64), nullable=False, index=True, server_default="default")
    user_id = Column(String(255), nullable=False, index=True)
    gateway = Column(String(50), nullable=False, default="mercadopago")
    card_token_id = Column(String(255), nullable=False)
    mp_customer_id = Column(String(255), nullable=True)
    mp_card_id = Column(String(255), nullable=True)
    last_four_digits = Column(String(4), nullable=False)
    payment_method_id = Column(String(50), nullable=False)
    cardholder_name = Column(String(255), nullable=False)
    expiration_month = Column(String(2), nullable=False)
    expiration_year = Column(String(4), nullable=False)
    is_default = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BillingCycle(Base):
    """Immutable usage snapshot used to explain every recurring charge."""

    __tablename__ = "billing_cycles"
    __table_args__ = (UniqueConstraint("subscription_id", "period_start", name="uq_billing_cycle_period"),)

    id = Column(Integer, primary_key=True, index=True)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id"), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    # What is being billed for in this period, in whatever unit the product
    # meters: active users, messages, gigabytes. The service never interprets
    # it, it only multiplies.
    quantity = Column(Integer, nullable=False)
    unit_amount = Column(Numeric(14, 2), nullable=False)
    minimum_amount = Column(Numeric(14, 2), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="ARS")
    status = Column(String(50), nullable=False, default="pending", index=True)
    gateway_authorized_payment_id = Column(String(255), nullable=True, unique=True, index=True)
    gateway_payment_id = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscription = relationship("Subscription")


class WebhookEvent(Base):
    """Provider delivery log. gateway_event_id makes retries idempotent."""

    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    gateway = Column(String(50), nullable=False)
    gateway_event_id = Column(String(255), nullable=False, unique=True, index=True)
    topic = Column(String(100), nullable=False)
    resource_id = Column(String(255), nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(String(30), nullable=False, default="received", index=True)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
