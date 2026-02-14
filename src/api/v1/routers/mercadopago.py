from typing import Annotated

from fastapi import APIRouter, Query, Depends, Request
from fastapi.responses import JSONResponse

from api.v1.dependencies.mercadopago import get_mp_service
from gateways.mercadopago.models import (
    IdentificationType,
    InstallmentsInfo,
    PaymentMethod,
    TokenDataInput,
    TokenResponse,
)
from gateways.mercadopago.constants import MIN_BIN_LENGTH
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.services import MercadopagoService


router = APIRouter()


async def mercado_pago_api_error_handler(
    request: Request, exc: MercadopagoAPIException
):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.error_code,
            "message": exc.error_msg,
        },
    )


@router.get("/payment_methods")
def get_payment_methods(
    mp: MercadopagoService = Depends(get_mp_service),
) -> list[PaymentMethod]:
    return mp.get_payment_methods()


@router.get("/payment_method")
def get_payment_method(
    bin: Annotated[str, Query(min_length=MIN_BIN_LENGTH)],
    mp: MercadopagoService = Depends(get_mp_service),
) -> PaymentMethod | None:
    return mp.get_payment_method(bin=bin)


@router.get("/installments")
def get_installments(
    bin: Annotated[str, Query(min_length=MIN_BIN_LENGTH)],
    amount: int,
    mp: MercadopagoService = Depends(get_mp_service),
) -> list[InstallmentsInfo]:
    return mp.get_installments(bin=bin, amount=amount)


@router.get("/identification_types")
def get_identification_types(
    mp: MercadopagoService = Depends(get_mp_service),
) -> list[IdentificationType]:
    return mp.get_identification_types()


@router.post("/token")
def create_token(
    token_data: TokenDataInput, mp: MercadopagoService = Depends(get_mp_service)
) -> TokenResponse:
    return mp.create_token(token_data=token_data)
