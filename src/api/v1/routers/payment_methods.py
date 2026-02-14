from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.v1.dependencies.subscriptions import get_db, get_mp_payment_service
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from schemas.payment_methods import PaymentMethodCreate, PaymentMethodResponse, PaymentMethodUpdate
from services.payment_method_service import PaymentMethodService

router = APIRouter()


def _service(db: Session = Depends(get_db)) -> PaymentMethodService:
    return PaymentMethodService(db=db)


@router.post("/", response_model=PaymentMethodResponse)
def create_payment_method(
    user_id: str = Query(...),
    data: PaymentMethodCreate = Body(...),
    service: PaymentMethodService = Depends(_service),
    mp: MercadopagoPaymentService = Depends(get_mp_payment_service),
):
    payment_method = service.create_payment_method(user_id, data, mp_payment_service=mp)
    return payment_method


@router.get("/user/{user_id}", response_model=list[PaymentMethodResponse])
def get_payment_methods_by_user(
    user_id: str,
    service: PaymentMethodService = Depends(_service),
):
    return service.get_payment_methods_by_user(user_id)


@router.get("/user/{user_id}/default", response_model=PaymentMethodResponse | None)
def get_default_payment_method(
    user_id: str,
    service: PaymentMethodService = Depends(_service),
):
    return service.get_default_payment_method(user_id)


@router.get("/{payment_method_id}", response_model=PaymentMethodResponse)
def get_payment_method(
    payment_method_id: int,
    user_id: str = Query(...),
    service: PaymentMethodService = Depends(_service),
):
    payment_method = service.get_payment_method(payment_method_id, user_id)
    if not payment_method:
        raise HTTPException(status_code=404, detail="payment_method_not_found")
    return payment_method


@router.put("/{payment_method_id}", response_model=PaymentMethodResponse)
def update_payment_method(
    payment_method_id: int,
    user_id: str = Query(...),
    data: PaymentMethodUpdate = Body(...),
    service: PaymentMethodService = Depends(_service),
):
    payment_method = service.update_payment_method(payment_method_id, user_id, data)
    if not payment_method:
        raise HTTPException(status_code=404, detail="payment_method_not_found")
    return payment_method


@router.delete("/{payment_method_id}")
def delete_payment_method(
    payment_method_id: int,
    user_id: str = Query(...),
    service: PaymentMethodService = Depends(_service),
):
    success = service.delete_payment_method(payment_method_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="payment_method_not_found")
    return {"message": "payment_method_deleted"}
