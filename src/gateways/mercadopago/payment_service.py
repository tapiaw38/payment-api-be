import uuid
import requests
from typing import Any

from .exceptions import MercadopagoAPIException
from .models import TokenDataInput
from .payment_models import PaymentCreate


class MercadopagoPaymentService:
    def __init__(self, access_token: str, checkout_pro_access_token: str = ""):
        self.access_token = access_token
        self.checkout_pro_access_token = checkout_pro_access_token or access_token
        self.base_url = "https://api.mercadopago.com/v1"

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    def _send_request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        kwargs = {"headers": self._headers(idempotency_key), "timeout": 30}
        if json_body is not None:
            kwargs["json"] = json_body
        response = requests.request(method, url, **kwargs)
        if not response.ok:
            raise MercadopagoAPIException(response)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def create_payment(self, data: PaymentCreate) -> dict[str, Any]:
        # When using a customer card (type="customer" + id), MP expects ONLY type+id in payer,
        # not email. Including email alongside type+id causes 500 internal_error.
        if data.payer.type == "customer" and data.payer.id:
            payer_obj: dict[str, Any] = {"type": "customer", "id": data.payer.id}
        else:
            payer_obj = {"email": data.payer.email}
            if data.payer.id:
                payer_obj["id"] = data.payer.id
            if data.payer.type:
                payer_obj["type"] = data.payer.type

        body: dict[str, Any] = {
            "transaction_amount": data.transaction_amount,
            "token": data.token,
            "payer": payer_obj,
            "installments": data.installments,
        }
        # MP infers payment_method_id from the token when using customer cards;
        # sending it alongside payer.type="customer" causes 500 internal_error.
        if data.payer.type != "customer":
            body["payment_method_id"] = data.payment_method_id
        if data.description:
            body["description"] = data.description
        if data.external_reference:
            body["external_reference"] = data.external_reference
        import json as _json
        print(f"[DEBUG] MP /payments request body: {_json.dumps(body)}", flush=True)
        return self._send_request("POST", "/payments", json_body=body, idempotency_key=str(uuid.uuid4()))

    def get_payment(self, payment_id: int | str) -> dict[str, Any]:
        return self._send_request("GET", f"/payments/{payment_id}")

    def get_or_create_customer(self, email: str) -> str:
        """Find existing MP customer by email or create new one. Returns customer_id."""
        resp = self._send_request("GET", f"/customers/search?email={email}")
        results = resp.get("results", [])
        if results:
            return results[0]["id"]
        resp = self._send_request("POST", "/customers", json_body={"email": email})
        return resp["id"]

    def save_card_to_customer(self, customer_id: str, token: str) -> str:
        """Save card to MP customer using a one-time token. Returns mp_card_id."""
        resp = self._send_request(
            "POST",
            f"/customers/{customer_id}/cards",
            json_body={"token": token},
        )
        return resp["id"]

    def get_customer_email(self, customer_id: str) -> str:
        """Get the email of an MP customer. Returns email."""
        resp = self._send_request("GET", f"/customers/{customer_id}")
        return resp.get("email", "")

    def create_card_token(self, token_data: TokenDataInput) -> str:
        """Create a card token server-side using the access_token. Returns token id."""
        year = token_data.card_expiration_year
        if len(year) == 2:
            year = "20" + year
        body = {
            "card_number": token_data.card_number,
            "security_code": token_data.security_code,
            "expiration_month": token_data.card_expiration_month,
            "expiration_year": year,
            "cardholder": {
                "name": token_data.cardholder_name,
                "identification": {
                    "type": token_data.doc_type or "DNI",
                    "number": token_data.doc_number or "",
                },
            },
        }
        resp = self._send_request("POST", "/card_tokens", json_body=body)
        return resp["id"]

    def create_preference(self, items: list[dict], payer_email: str, external_reference: str, back_urls: dict, notification_url: str | None = None) -> dict[str, Any]:
        """Create a Checkout Pro preference. Returns preference with init_point."""
        body: dict[str, Any] = {
            "items": items,
            "payer": {"email": payer_email},
            "external_reference": external_reference,
        }
        # Only add back_urls and auto_return if success URL is a proper public URL
        success_url = back_urls.get("success", "")
        if success_url and not success_url.startswith("http://localhost") and "localhost" not in success_url:
            body["back_urls"] = back_urls
            body["auto_return"] = "approved"
        if notification_url:
            body["notification_url"] = notification_url
        # Preferences endpoint lives at /checkout/preferences, outside the /v1 prefix
        # Uses the Checkout Pro access token (different app/credentials from Checkout API)
        checkout_base = self.base_url.replace("/v1", "")
        url = f"{checkout_base}/checkout/preferences"
        idempotency_key = str(uuid.uuid4())
        headers = {
            "Authorization": f"Bearer {self.checkout_pro_access_token}",
            "Content-Type": "application/json",
            "X-Idempotency-Key": idempotency_key,
        }
        import requests as _requests
        response = _requests.post(url, headers=headers, json=body, timeout=30)
        if not response.ok:
            from .exceptions import MercadopagoAPIException
            raise MercadopagoAPIException(response)
        return response.json()

    def create_card_token_from_saved(self, customer_id: str, card_id: str, security_code: str | None = None) -> str:
        """Create a fresh card token from a saved customer card. Returns token id."""
        body = {"customer_id": customer_id, "card_id": card_id}
        if security_code:
            body["security_code"] = security_code
        resp = self._send_request(
            "POST",
            "/card_tokens",
            json_body=body,
            idempotency_key=str(uuid.uuid4()),
        )
        return resp["id"]
