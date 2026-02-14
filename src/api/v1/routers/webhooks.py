from fastapi import APIRouter, Request, BackgroundTasks

from config.settings import settings
from db.session import SessionLocal
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from services.payment_service import PaymentService
from services.subscription_service import SubscriptionService

router = APIRouter()


def _update_subscription_from_mp(gateway_subscription_id: str, status: str):
    db = SessionLocal()
    try:
        mp = MercadopagoSubscriptionService(access_token=settings.mercadopago_access_token)
        svc = SubscriptionService(db=db, mp_subscription=mp)
        svc.update_subscription_status(gateway_subscription_id, status)
    finally:
        db.close()


def _update_payment_from_mp(gateway_payment_id: str, status: str):
    db = SessionLocal()
    try:
        mp = MercadopagoPaymentService(access_token=settings.mercadopago_access_token)
        svc = PaymentService(db=db, mp_payment=mp)
        svc.update_payment_status(gateway_payment_id, status)
    finally:
        db.close()


@router.post("/mercadopago")
async def mercadopago_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    body = await request.json()
    topic = body.get("type") or body.get("topic")
    data = body.get("data") or {}
    resource_id = data.get("id") if isinstance(data, dict) else body.get("id")
    if not resource_id:
        return {"ok": False}
    if topic == "payment":
        try:
            mp = MercadopagoPaymentService(access_token=settings.mercadopago_access_token)
            result = mp.get_payment(str(resource_id))
            status = result.get("status", "")
            if status:
                background_tasks.add_task(_update_payment_from_mp, str(resource_id), status)
        except Exception:
            pass
    elif topic in ("preapproval", "authorized_payment"):
        try:
            mp = MercadopagoSubscriptionService(access_token=settings.mercadopago_access_token)
            result = mp.get_subscription(str(resource_id))
            status = result.get("status", "")
            if status:
                background_tasks.add_task(_update_subscription_from_mp, str(resource_id), status)
        except Exception:
            pass
    return {"ok": True}
