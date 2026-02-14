from config.settings import settings
from gateways.mercadopago.services import MercadopagoService, FakeMercadopagoService


def get_mp_service(fake=False) -> MercadopagoService:
    if fake:
        return FakeMercadopagoService()
    else:
        public_key = settings.public_keys_by_gateway.get("mercadopago")
        return MercadopagoService(public_key=public_key)
