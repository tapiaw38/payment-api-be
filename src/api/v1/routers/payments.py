from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.v1.dependencies.subscriptions import get_db, get_mp_payment_service
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.payment_service import MercadopagoPaymentService
from schemas.payments import PaymentCreate, PaymentResponse, PaymentWithSavedMethodCreate, PreferenceCreate, PreferenceResponse
from services.payment_service import PaymentService
from sqlalchemy.orm import Session

router = APIRouter()


def _service(
    request: Request,
    db: Session = Depends(get_db),
    mp: MercadopagoPaymentService = Depends(get_mp_payment_service),
) -> PaymentService:
    return PaymentService(
        db=db,
        mp_payment=mp,
        tenant=getattr(request.state, "tenant", "default"),
    )


@router.post("/", response_model=PaymentResponse)
def create_payment(
    data: PaymentCreate,
    service: PaymentService = Depends(_service),
):
    try:
        payment = service.create_payment(data)
        return payment
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MercadopagoAPIException as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.error_code, "message": e.error_msg},
        )


@router.post("/with-saved-method", response_model=PaymentResponse)
def create_payment_with_saved_method(
    data: PaymentWithSavedMethodCreate,
    service: PaymentService = Depends(_service),
):
    try:
        payment = service.create_payment_with_saved_method(data)
        return payment
    except ValueError as e:
        status = 409 if str(e) == "payment_reference_conflict" else 400
        raise HTTPException(status_code=status, detail=str(e))
    except MercadopagoAPIException as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.error_code, "message": e.error_msg},
        )


@router.post("/preferences", response_model=PreferenceResponse)
def create_preference(
    data: PreferenceCreate,
    service: PaymentService = Depends(_service),
):
    try:
        return service.create_preference(data)
    except MercadopagoAPIException as e:
        raise HTTPException(
            status_code=e.status_code,
            detail={"code": e.error_code, "message": e.error_msg},
        )


@router.get("/{payment_id}", response_model=PaymentResponse)
def get_payment(
    payment_id: int,
    user_id: str = Query(...),
    service: PaymentService = Depends(_service),
):
    payment = service.get_payment(payment_id, user_id=user_id)
    if not payment:
        raise HTTPException(status_code=404, detail="payment_not_found")
    return payment
