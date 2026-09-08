"""Mercado Pago webhook ingestion with signature verification and audit log."""

import hashlib
import hmac
import json
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


def _signature_parts(value: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in value.split(",") if "=" in part)


def _validate_signature(request: Request, data_id: str) -> bool:
    secret = settings.mercadopago_webhook_secret
    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    if not secret or not signature or not request_id or not data_id:
        return False
    parts = _signature_parts(signature)
    timestamp, received_hash = parts.get("ts"), parts.get("v1")
    if not timestamp or not received_hash:
        return False
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_hash)


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
    if not _validate_signature(request, resource_id):
        raise HTTPException(status_code=401, detail="invalid_webhook_signature")

    db = SessionLocal()
    try:
        event = _record_event(db, body, topic, resource_id)
        if event is None:
            return {"ok": True, "duplicate": True}
        try:
            _process_event(db, topic, resource_id)
        except Exception:
            event.status = "failed"
            db.commit()
            raise HTTPException(status_code=503, detail="webhook_processing_failed")
        event.status = "processed"
        event.processed_at = datetime.utcnow()
        db.commit()
        return {"ok": True}
    finally:
        db.close()
