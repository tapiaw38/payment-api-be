from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Enum as SQLEnum
from sqlalchemy.orm import relationship

from .session import Base


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(String(500), nullable=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="ARS")
    interval = Column(String(20), nullable=False)
    interval_count = Column(Integer, default=1)
    gateway = Column(String(50), nullable=False, default="mercadopago")
    gateway_plan_id = Column(String(255), nullable=True, index=True)
    active = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscriptions = relationship("Subscription", back_populates="plan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    user_id = Column(String(255), nullable=False, index=True)
    gateway = Column(String(50), nullable=False, default="mercadopago")
    gateway_subscription_id = Column(String(255), nullable=True, index=True)
    status = Column(String(50), nullable=False, default="pending", index=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Integer, default=0)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("Plan", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    gateway = Column(String(50), nullable=False, default="mercadopago")
    gateway_payment_id = Column(String(255), nullable=True, index=True)
    amount = Column(Float, nullable=False)
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
