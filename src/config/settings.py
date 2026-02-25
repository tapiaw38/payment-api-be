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

    api_key: str = os.environ.get("PAYMENTS_API_KEY", "")

    mercadopago_public_key: str = os.environ.get("MP_PUBLIC_KEY_AR", "")
    mercadopago_access_token: str = os.environ.get("MP_ACCESS_TOKEN", "")
    mercadopago_checkout_pro_access_token: str = os.environ.get("MP_CHECKOUT_PRO_ACCESS_TOKEN", "")

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
