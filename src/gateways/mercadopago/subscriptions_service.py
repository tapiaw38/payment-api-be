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
        back_url: str,
        currency: str = "ARS",
        frequency: int = 1,
        frequency_type: str = "months",
    ) -> dict[str, Any]:
        """Publishes a plan.

        back_url is required by the gateway and was missing, which is why every
        creation came back as "Parameters passed are invalid" — a message that
        names nothing. Sent on its own the request says "Back url is required",
        which is how this was found.
        """
        body = {
            "reason": reason,
            "auto_recurring": {
                "frequency": frequency,
                "frequency_type": frequency_type,
                "transaction_amount": amount,
                "currency_id": currency,
            },
            "back_url": back_url,
            # An object with payment_types and payment_methods. The list form
            # this used to send is rejected the same silent way.
            "payment_methods_allowed": {
                "payment_types": [{"id": "credit_card"}, {"id": "debit_card"}],
                "payment_methods": [],
            },
        }
        return self._send_request("POST", "/preapproval_plan", json_body=body)

    def update_plan(
        self,
        preapproval_plan_id: str,
        reason: str | None = None,
        amount: float | None = None,
        currency: str = "ARS",
    ) -> dict[str, Any]:
        """Edits the published plan.

        This changes what new subscribers are charged. People already
        subscribed hold their own preapproval with its own amount and keep
        paying it until that subscription is updated too.
        """
        body: dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        if amount is not None:
            body["auto_recurring"] = {"transaction_amount": amount, "currency_id": currency}
        return self._send_request("PUT", f"/preapproval_plan/{preapproval_plan_id}", json_body=body)

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

    def get_authorized_payment(self, authorized_payment_id: str) -> dict[str, Any]:
        return self._send_request("GET", f"/authorized_payments/{authorized_payment_id}")

    def update_subscription_amount(
        self,
        preapproval_id: str,
        amount: float,
        currency: str = "ARS",
    ) -> dict[str, Any]:
        return self._send_request(
            "PUT",
            f"/preapproval/{preapproval_id}",
            json_body={"auto_recurring": {"transaction_amount": amount, "currency_id": currency}},
        )

    def cancel_subscription(self, preapproval_id: str) -> dict[str, Any]:
        return self._send_request("PUT", f"/preapproval/{preapproval_id}", json_body={"status": "canceled"})

    def pause_subscription(self, preapproval_id: str) -> dict[str, Any]:
        """Stops charging without ending the agreement.

        Unlike cancelling, this can be undone: the payer keeps their
        authorisation and resume_subscription puts it back to work.
        """
        return self._send_request("PUT", f"/preapproval/{preapproval_id}", json_body={"status": "paused"})

    def resume_subscription(self, preapproval_id: str) -> dict[str, Any]:
        return self._send_request("PUT", f"/preapproval/{preapproval_id}", json_body={"status": "authorized"})
