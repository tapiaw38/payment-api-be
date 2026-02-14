import requests
from typing import Any

from .exceptions import MercadopagoAPIException
from .models import (
    PaymentMethod,
    InstallmentsInfo,
    IdentificationType,
    TokenDataInput,
    TokenResponse,
)


class MercadopagoService:
    def __init__(self, public_key: str):
        self.public_key: str = public_key
        self.base_url: str = "https://api.mercadopago.com/v1"

    def _send_request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = {},
        data: dict[str, Any] | None = {},
    ):
        params["public_key"] = self.public_key
        response = requests.request(method, url, params=params, data=data)
        if not response.ok:
            raise MercadopagoAPIException(response)
        return response.json()

    def get_payment_methods(self) -> list[PaymentMethod]:
        url = f"{self.base_url}/payment_methods"
        payment_methods = self._send_request("GET", url)
        return payment_methods

    def get_payment_method(self, bin: str) -> PaymentMethod | None:
        params = {"bins": bin, "marketplace": "NONE"}
        url = f"{self.base_url}/payment_methods/search"
        payment_method = self._send_request("GET", url, params=params)["results"]
        return payment_method[0] if payment_method else None

    def get_installments(self, bin: str, amount: float) -> list[InstallmentsInfo]:
        params = {
            "bin": bin,
            "amount": amount,
        }
        url = f"{self.base_url}/payment_methods/installments"
        installments = self._send_request("GET", url, params=params)
        return installments

    def get_identification_types(self) -> list[IdentificationType]:
        url = f"{self.base_url}/identification_types"
        identification_types = self._send_request("GET", url)
        return identification_types

    def create_token(self, token_data: TokenDataInput) -> TokenResponse:
        url = f"{self.base_url}/card_tokens"
        body = {
            "card_number": token_data.card_number,
            "security_code": token_data.security_code,
            "card_expiration_month": token_data.card_expiration_month,
            "card_expiration_year": token_data.card_expiration_year,
            "cardholder": {
                "name": token_data.cardholder_name,
                "identification": {
                    "type": token_data.doc_type or "DNI",
                    "number": token_data.doc_number or "",
                },
            },
        }
        params = {"public_key": self.public_key}
        response = requests.post(url, json=body, params=params)
        if not response.ok:
            raise MercadopagoAPIException(response)
        return response.json()


class FakeMercadopagoService:
    def get_payment_methods(self) -> list[PaymentMethod]:
        return [PaymentMethod.Config.schema_extra]

    def get_payment_method(self, bin: str) -> PaymentMethod:
        return PaymentMethod.Config.schema_extra

    def get_installments(self, bin: int, amount: float) -> list[InstallmentsInfo]:
        return [InstallmentsInfo.Config.schema_extra]

    def get_identification_types(self) -> list[IdentificationType]:
        return [IdentificationType.Config.schema_extra]

    def create_token(self, token_data) -> TokenResponse:
        return TokenResponse.Config.schema_extra
