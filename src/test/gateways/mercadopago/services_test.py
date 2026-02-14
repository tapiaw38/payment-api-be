import responses

from api.v1.dependencies.mercadopago import get_mp_service
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.services import MercadopagoService


class TestMercadopagoService:
    def setup_method(self):
        self.mp: MercadopagoService = get_mp_service()
        self.base_url: str = "https://api.mercadopago.com/v1"

    @responses.activate
    def test_get_payment_method_invalid_bin(self):
        url = f"{self.base_url}/payment_methods/search"
        responses.add(
            responses.GET,
            url,
            json={"code": "bad_request", "message": "Invalid bin"},
            status=400,
        )
        try:
            self.mp.get_payment_method(bin="1234")
        except MercadopagoAPIException as e:
            assert e.status_code == 400
            assert e.error_code == "bad_request"
            assert e.error_msg == "Invalid bin"
        else:
            assert False, "Expected MercadopagoAPIException not raised"

    @responses.activate
    def test_get_installments_invalid_amount(self):
        url = f"{self.base_url}/payment_methods/installments"
        responses.add(
            responses.GET,
            url,
            json={"code": "bad_request", "message": "Invalid amount"},
            status=400,
        )
        try:
            self.mp.get_installments(bin="123456", amount="asd")
        except MercadopagoAPIException as e:
            assert e.status_code == 400
            assert e.error_code == "bad_request"
            assert e.error_msg == "Invalid amount"
        else:
            assert False, "Expected MercadopagoAPIException not raised"
