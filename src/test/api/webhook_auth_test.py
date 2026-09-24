"""The webhook endpoint must answer Mercado Pago, which holds no API key.

Regression: with ROOT_PATH set, the middleware compared request.url.path —
which Starlette builds as root_path + path — against the exempt prefixes, so
the exemption silently stopped applying and every real notification got 401
before its signature was checked.
"""

import os

from fastapi.testclient import TestClient

os.environ.setdefault("PAYMENTS_API_KEYS", "test:test-key")


def _client(root_path: str) -> TestClient:
    from api.main import app

    app.root_path = root_path
    return TestClient(app, root_path=root_path)


def test_webhook_is_reached_without_an_api_key():
    for root_path in ("", "/payments"):
        response = _client(root_path).post(
            "/api/v1/webhooks/mercadopago", json={}
        )
        # 400 means the handler ran and rejected the empty body. A 401 would
        # mean the middleware answered first and asked Mercado Pago for a key.
        assert response.status_code == 400, (root_path, response.status_code)
        assert response.json()["detail"] == "missing_webhook_resource"


def test_other_routes_still_require_a_key():
    for root_path in ("", "/payments"):
        response = _client(root_path).get("/api/v1/subscriptions/plans")
        assert response.status_code == 401, (root_path, response.status_code)
