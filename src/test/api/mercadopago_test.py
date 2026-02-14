from types import UnionType
from typing import get_args, get_origin

import fastapi
from fastapi.routing import APIRoute
import pydantic
import pytest
import responses

from api.v1.dependencies.mercadopago import get_mp_service
from gateways.mercadopago.models import TokenDataInput
from test.conftest import client


app = client.app
# this file tests the endpoints, so we use the fake mercadopagoservice class.
# in the service tests we use the real one and we fake the mercadopago responses.
app.dependency_overrides[get_mp_service] = lambda: get_mp_service(fake=True)


class TestMercadopagoApi:
    def setup_method(self):
        self.base_url_mercadopago: str = "/api/v1/mercadopago"

    def _get_valid_query_params(self, endpoint_path: str):
        endpoint_path = endpoint_path.replace("/api/v1", "")

        params_per_endpoint = {
            "/mercadopago/payment_method": {"bin": "123456"},
            "/mercadopago/installments": {"bin": "123456", "amount": 10},
        }
        return params_per_endpoint.get(endpoint_path, {})

    def _get_valid_json_bodies(self, endpoint_path: str):
        endpoint_path = endpoint_path.replace("/api/v1", "")

        json_body_per_endpoint = {
            "/mercadopago/token": TokenDataInput.Config.schema_extra
        }

        return json_body_per_endpoint.get(endpoint_path, {})

    def _get_route_response_model(self, route: APIRoute):
        """
        this method asumes the possible return types of a testable endpoint are:
            * a pydantic model
            * a list of pydantic models
            * a union of a pydantic model and None
        and then extracts and returns the final pydantic model.
        """
        response_origin = get_origin(route.response_model)
        if response_origin is list:
            return get_args(route.response_model)[0]
        elif response_origin is UnionType:
            union_args = get_args(route.response_model)
            for arg in union_args:
                if arg is not type(None):
                    return arg
            return None
        else:
            return route.response_model

    def test_endpoints_response_model(self):
        all_routes = client.app.routes
        test_api_routes = [
            route for route in all_routes if isinstance(route, fastapi.routing.APIRoute)
        ]
        for route in test_api_routes:
            model = self._get_route_response_model(route)
            if model:
                params = self._get_valid_query_params(route.path)
                if "POST" in route.methods:
                    json_body = self._get_valid_json_bodies(route.path)
                    response = client.post(
                        url=route.path,
                        params=params,
                        json=json_body,
                    )
                elif "GET" in route.methods:
                    response = client.get(
                        url=route.path,
                        params=params,
                    )

                assert response.status_code == 200

                response_json = response.json()
                if isinstance(response_json, list):
                    response = response_json[0]
                else:
                    response = response_json

                try:
                    model.parse_obj(response)
                except pydantic.ValidationError:
                    pytest.fail(f"Response is not a valid {model.__name__} object")

    def test_get_payment_method_invalid_bin(self):
        url = f"{self.base_url_mercadopago}/payment_method"

        response = client.get(
            url=url,
            params={"bin": "123"},
        )

        assert response.status_code == 422

    def test_get_installments_invalid_params(self):
        url = f"{self.base_url_mercadopago}/installments"

        invalid_bin_response = client.get(
            url=url,
            params={"bin": "123", "amount": 100},
        )
        invalid_amount_response = client.get(
            url=url,
            params={"bin": "123456", "amount": "asd"},
        )

        assert invalid_bin_response.status_code == 422
        assert invalid_amount_response.status_code == 422

    def test_create_token_invalid_token_input(self):
        url = f"{self.base_url_mercadopago}/token"

        response = client.post(
            url=url,
            json={"card_number": "123456"},
        )

        assert response.status_code == 422

    @responses.activate
    def test_json_reponse_for_400_mercadopago_response(self):
        # remove the override because here we want the real service
        app.dependency_overrides = {}

        # mock the mercadopago response that will be handled by the mercadopagoservice
        mock_mercadopago_url = "https://api.mercadopago.com/v1/payment_methods/search"
        responses.add(
            responses.GET,
            mock_mercadopago_url,
            json={"code": "bad_request", "message": "Invalid bin"},
            status=400,
        )

        # call the endpoint that triggers the mercadopagoservice methods
        endpoint_url = f"{self.base_url_mercadopago}/payment_method/"
        response = client.get(
            url=endpoint_url,
            params={"bin": "123456789"},
        )

        assert response.status_code == 400
        assert response.json().get("code") == "bad_request"
        assert response.json().get("message") == "Invalid bin"
