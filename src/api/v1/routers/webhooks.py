"""Mercado Pago webhook ingestion with signature verification and audit log."""

import hashlib
import hmac
import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from config.settings import settings
from db.models import WebhookEvent
from db.session import SessionLocal
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from services.payment_service import PaymentService
from services.subscription_service import SubscriptionService

router = APIRouter()
# Uvicorn configures this logger in production; using it makes rejected
# webhook reasons visible in container logs without exposing signatures.
logger = logging.getLogger("uvicorn.error")


def _signature_parts(value: str) -> dict[str, str]:
    # Mercado Pago sends a comma-separated header. Some proxies preserve the
    # optional space after the comma ("ts=..., v1=..."); stripping both sides
    # prevents a valid signature from being read as a missing `v1` field.
    parts: dict[str, str] = {}
    for part in value.split(","):
        if "=" not in part:
            continue
        key, item = part.split("=", 1)
        key, item = key.strip(), item.strip()
        if key and item:
            parts[key] = item
    return parts


def _validate_signature(request: Request, data_id: str) -> tuple[bool, str]:
    secret = settings.mercadopago_webhook_secret
    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    if not secret:
        return False, "webhook_secret_missing"
    if not signature:
        return False, "signature_missing"
    if not request_id:
        return False, "request_id_missing"
    if not data_id:
        return False, "resource_id_missing"
    parts = _signature_parts(signature)
    timestamp, received_hash = parts.get("ts"), parts.get("v1")
    if not timestamp or not received_hash:
        return False, "signature_parts_missing"
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_hash), "signature_mismatch"


def _record_event(db, body: dict, topic: str, resource_id: str) -> WebhookEvent | None:
    event_id = str(body.get("id", ""))
    if not event_id:
        raise HTTPException(status_code=400, detail="missing_webhook_event_id")
    event = WebhookEvent(
        gateway="mercadopago", gateway_event_id=event_id, topic=topic,
        resource_id=resource_id, payload=json.dumps(body, separators=(",", ":")),
    )
    db.add(event)
    try:
        db.commit()
        db.refresh(event)
        return event
    except IntegrityError:
        db.rollback()
        existing = db.query(WebhookEvent).filter(WebhookEvent.gateway_event_id == event_id).first()
        return None if existing and existing.status == "processed" else existing


def _process_event(db, topic: str, resource_id: str) -> None:
    subscriptions = MercadopagoSubscriptionService(access_token=settings.mercadopago_access_token)
    if topic == "payment":
        payments = MercadopagoPaymentService(access_token=settings.mercadopago_access_token)
        result = payments.get_payment(resource_id)
        PaymentService(db=db, mp_payment=payments).update_payment_status(resource_id, result.get("status", ""))
        SubscriptionService(db=db, mp_subscription=subscriptions).sync_recurring_payment(result)
    elif topic == "subscription_preapproval":
        result = subscriptions.get_subscription(resource_id)
        SubscriptionService(db=db, mp_subscription=subscriptions).sync_subscription_from_gateway(result)
    elif topic == "subscription_authorized_payment":
        result = subscriptions.get_authorized_payment(resource_id)
        SubscriptionService(db=db, mp_subscription=subscriptions).sync_authorized_payment(result)


@router.post("/mercadopago")
async def mercadopago_webhook(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json")
    topic = body.get("type") or body.get("topic")
    data = body.get("data") or {}
    resource_id = str(request.query_params.get("data.id") or (data.get("id") if isinstance(data, dict) else "") or "")
    if not topic or not resource_id:
        raise HTTPException(status_code=400, detail="missing_webhook_resource")
    valid_signature, rejection_reason = _validate_signature(request, resource_id)
    if not valid_signature:
        # Keep enough context to diagnose configuration/header formatting
        # without logging signature material, tokens or the full payload.
        logger.warning(
            "Mercado Pago webhook signature rejected topic=%s resource=%s reason=%s",
            topic,
            resource_id,
            rejection_reason,
        )
        raise HTTPException(status_code=401, detail="invalid_webhook_signature")

    db = SessionLocal()
    try:
        event = _record_event(db, body, topic, resource_id)
        if event is None:
            return {"ok": True, "duplicate": True}
        try:
            _process_event(db, topic, resource_id)
        except Exception:
            # Mercado Pago retries a 503 for days, so a failure that leaves no
            # trace is a loop nobody can diagnose. The traceback carries no
            # signature material, tokens or card data.
            logger.exception(
                "Mercado Pago webhook processing failed topic=%s resource=%s",
                topic,
                resource_id,
            )
            event.status = "failed"
            db.commit()
            raise HTTPException(status_code=503, detail="webhook_processing_failed")
        event.status = "processed"
        event.processed_at = datetime.utcnow()
        db.commit()
        return {"ok": True}
    finally:
        db.close()
