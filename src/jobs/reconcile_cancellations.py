"""Cancel subscriptions whose paid period ended after a requested cancellation.

Run once daily from Cron, Kubernetes CronJob, or systemd timer:
    python -m jobs.reconcile_cancellations
"""

from config.settings import settings
from db.session import SessionLocal
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from services.subscription_service import SubscriptionService


def main() -> None:
    db = SessionLocal()
    try:
        service = SubscriptionService(
            db=db,
            mp_subscription=MercadopagoSubscriptionService(settings.mercadopago_access_token),
            webhook_url=settings.mercadopago_subscription_webhook_url,
        )
        cancelled = service.cancel_due_subscriptions()
        print(f"cancelled_subscriptions={len(cancelled)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
