from fastapi import APIRouter, Depends, HTTPException

from api.v1.dependencies.subscriptions import get_db, get_mp_subscription_service
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from schemas.subscriptions import PlanCreate, PlanResponse, SubscriptionCreate, SubscriptionResponse
from services.subscription_service import SubscriptionService
from sqlalchemy.orm import Session

router = APIRouter()


def _service(
    db: Session = Depends(get_db),
    mp: MercadopagoSubscriptionService = Depends(get_mp_subscription_service),
) -> SubscriptionService:
    return SubscriptionService(db=db, mp_subscription=mp)


@router.get("/plans", response_model=list[PlanResponse])
def list_plans(
    active_only: bool = True,
    service: SubscriptionService = Depends(_service),
):
    return service.list_plans(active_only=active_only)


@router.post("/plans", response_model=PlanResponse)
def create_plan(
    data: PlanCreate,
    service: SubscriptionService = Depends(_service),
):
    try:
        plan = service.create_plan(data)
        return plan
    except MercadopagoAPIException as e:
        raise HTTPException(status_code=e.status_code, detail={"code": e.error_code, "message": e.error_msg})


@router.get("/plans/{plan_id}", response_model=PlanResponse)
def get_plan(
    plan_id: int,
    service: SubscriptionService = Depends(_service),
):
    plan = service.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="plan_not_found")
    return plan


@router.post("/subscriptions", response_model=SubscriptionResponse)
def create_subscription(
    data: SubscriptionCreate,
    service: SubscriptionService = Depends(_service),
):
    try:
        sub = service.create_subscription(data)
        return sub
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e.args[0]))
    except MercadopagoAPIException as e:
        raise HTTPException(status_code=e.status_code, detail={"code": e.error_code, "message": e.error_msg})


@router.get("/subscriptions/{subscription_id}", response_model=SubscriptionResponse)
def get_subscription(
    subscription_id: int,
    service: SubscriptionService = Depends(_service),
):
    sub = service.get_subscription(subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    return sub


@router.get("/subscriptions/user/{user_id}", response_model=list[SubscriptionResponse])
def list_subscriptions_by_user(
    user_id: str,
    service: SubscriptionService = Depends(_service),
):
    return service.get_subscription_by_user(user_id)


@router.post("/subscriptions/{subscription_id}/cancel")
def cancel_subscription(
    subscription_id: int,
    at_period_end: bool = False,
    service: SubscriptionService = Depends(_service),
):
    try:
        sub = service.cancel_subscription(subscription_id, at_period_end=at_period_end)
        if not sub:
            raise HTTPException(status_code=404, detail="subscription_not_found")
        return {"status": sub.status}
    except MercadopagoAPIException as e:
        raise HTTPException(status_code=e.status_code, detail={"code": e.error_code, "message": e.error_msg})
