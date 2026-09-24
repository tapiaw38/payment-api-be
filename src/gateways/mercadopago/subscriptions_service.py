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

    # The only three a preapproval plan accepts: the gateway answers
    # "Invalid value for payment_types" to anything else, prepaid_card
    # included, which is why a prepaid card fails as a rejected first charge
    # rather than at the form.
    ALLOWED_PAYMENT_TYPES = [
        {"id": "credit_card"},
        {"id": "debit_card"},
        {"id": "account_money"},
    ]

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
                "payment_types": self.ALLOWED_PAYMENT_TYPES,
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
        # Restated on every edit: a plan published before a payment type was
        # allowed keeps refusing it forever otherwise, and the refusal only
        # ever surfaces as somebody's rejected first charge.
        body: dict[str, Any] = {
            "payment_methods_allowed": {
                "payment_types": self.ALLOWED_PAYMENT_TYPES,
                "payment_methods": [],
            }
        }
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

    def create_pending_subscription(
        self,
        reason: str,
        payer_email: str,
        amount: float,
        back_url: str,
        external_reference: str | None = None,
        notification_url: str | None = None,
        currency: str = "ARS",
        frequency: int = 1,
        frequency_type: str = "months",
    ) -> dict[str, Any]:
        """Opens an agreement the payer authorises at Mercado Pago.

        Returns an init_point to send them to, where they pick a card or their
        account balance. The amount is restated here instead of naming the
        plan: a preapproval that points at a preapproval_plan_id is refused
        without a card_token_id, which is the whole thing we are avoiding.

        Nothing is charged until the payer authorises, and external_reference
        survives the round trip, so the webhook can find our row again.
        """
        body: dict[str, Any] = {
            "reason": reason,
            "payer_email": payer_email,
            "status": "pending",
            "back_url": back_url,
            "auto_recurring": {
                "frequency": frequency,
                "frequency_type": frequency_type,
                "transaction_amount": amount,
                "currency_id": currency,
            },
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
