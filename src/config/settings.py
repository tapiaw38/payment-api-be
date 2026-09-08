import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseSettings


load_dotenv()


class Settings(BaseSettings):
    ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")

    PROJECT_NAME: str = "payments"
    PROJECT_VERSION: str = "0.1.0"
    CONTACT: dict[str, str | Any] = {"back_alerts": "sysadmin@nymia.com.ar"}
    OPEN_API_URL: str = "/docs/openapi.json"
    if ENVIRONMENT != "dev":
        ROOT_PATH = "/payments"
    else:
        ROOT_PATH = ""

    # One key per calling product: "practiq:key1,yego:key2". The key is what
    # says who is asking, so it decides both whether a request is allowed and
    # which tenant's rows it can see.
    #
    # PAYMENTS_API_KEY is still read for the single-product deployments that
    # predate this, and maps to the "default" tenant.
    api_keys_raw: str = os.environ.get("PAYMENTS_API_KEYS", "")
    api_key: str = os.environ.get("PAYMENTS_API_KEY", "")

    @property
    def tenant_by_api_key(self) -> dict[str, str]:
        keys: dict[str, str] = {}
        for entry in self.api_keys_raw.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            tenant, _, key = entry.partition(":")
            tenant, key = tenant.strip(), key.strip()
            if tenant and key:
                keys[key] = tenant
        if self.api_key:
            keys.setdefault(self.api_key, "default")
        return keys

    mercadopago_public_key: str = os.environ.get("MP_PUBLIC_KEY_AR", "")
    mercadopago_access_token: str = os.environ.get("MP_ACCESS_TOKEN", "")
    mercadopago_checkout_pro_access_token: str = os.environ.get("MP_CHECKOUT_PRO_ACCESS_TOKEN", "")
    # Secret generated in Mercado Pago > Your integrations > Webhooks.
    # Webhook processing is intentionally disabled when this is absent.
    mercadopago_webhook_secret: str = os.environ.get("MP_WEBHOOK_SECRET", "")
    # Subscription notifications must target this service, never a URL supplied
    # by a browser/client request.
    mercadopago_subscription_webhook_url: str = os.environ.get("MP_SUBSCRIPTION_WEBHOOK_URL", "")
    # Where the gateway sends the payer back after they authorise. Required by
    # the gateway when publishing a plan, and per deployment because it points
    # at the product's own screen.
    mercadopago_back_url: str = os.environ.get("MP_BACK_URL", "")

    database_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql://payments:payments@localhost:5432/payments",
    )

    @property
    def public_keys_by_gateway(self) -> dict[str, str]:
        return {
            "mercadopago": self.mercadopago_public_key,
        }


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()
