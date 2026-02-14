import requests
from typing import Any

from .exceptions import MercadopagoAPIException
from .subscriptions_models import MPPlanCreate, MPSubscriptionCreate, MPSubscriptionResponse


class MercadopagoSubscriptionService:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://api.mercadopago.com"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _send_request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        kwargs = {"headers": self._headers(), "timeout": 30}
        if json_body is not None:
            kwargs["json"] = json_body
        if params:
            kwargs["params"] = params
        response = requests.request(method, url, **kwargs)
        if not response.ok:
            raise MercadopagoAPIException(response)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def create_plan(
        self,
        reason: str,
        amount: float,
        currency: str = "ARS",
        frequency: int = 1,
        frequency_type: str = "months",
    ) -> dict[str, Any]:
        body = {
            "reason": reason,
            "auto_recurring": {
                "frequency": frequency,
                "frequency_type": frequency_type,
                "transaction_amount": amount,
                "currency_id": currency,
            },
            "payment_methods_allowed": [{"payment_type_id": "credit_card"}, {"payment_type_id": "debit_card"}],
        }
        return self._send_request("POST", "/preapproval_plan", json_body=body)

    def create_subscription(
        self,
        preapproval_plan_id: str,
        reason: str,
        payer_email: str,
        card_token_id: str,
        external_reference: str | None = None,
        notification_url: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "preapproval_plan_id": preapproval_plan_id,
            "reason": reason,
            "payer_email": payer_email,
            "card_token_id": card_token_id,
            "status": "authorized",
        }
        if external_reference:
            body["external_reference"] = external_reference
        if notification_url:
            body["notification_url"] = notification_url
        return self._send_request("POST", "/preapproval", json_body=body)

    def get_subscription(self, preapproval_id: str) -> dict[str, Any]:
        return self._send_request("GET", f"/preapproval/{preapproval_id}")

    def cancel_subscription(self, preapproval_id: str) -> dict[str, Any]:
        return self._send_request("PUT", f"/preapproval/{preapproval_id}", json_body={"status": "cancelled"})

    def pause_subscription(self, preapproval_id: str) -> dict[str, Any]:
        return self._send_request("PUT", f"/preapproval/{preapproval_id}", json_body={"status": "paused"})
