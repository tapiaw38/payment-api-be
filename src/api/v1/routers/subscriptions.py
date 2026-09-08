from fastapi import APIRouter, Depends, HTTPException, Request

from api.v1.dependencies.subscriptions import get_db, get_mp_subscription_service
from config.settings import settings
from gateways.mercadopago.exceptions import MercadopagoAPIException
from gateways.mercadopago.subscriptions_service import MercadopagoSubscriptionService
from schemas.subscriptions import (
    BillingCycleCreate,
    BillingCycleResponse,
    EntitlementResponse,
    PlanCreate,
    PlanResponse,
    PlanUpdate,
    SubscriptionCreate,
    SubscriptionResponse,
)
from services.subscription_service import SubscriptionService
from sqlalchemy.orm import Session

router = APIRouter()


def _service(
    request: Request,
    db: Session = Depends(get_db),
    mp: MercadopagoSubscriptionService = Depends(get_mp_subscription_service),
) -> SubscriptionService:
    return SubscriptionService(
        db=db,
        mp_subscription=mp,
        webhook_url=settings.mercadopago_subscription_webhook_url,
        # Set by the API key middleware. Reading it from the request body
        # instead would let a caller name a tenant it has no key for.
        tenant=getattr(request.state, "tenant", "default"),
    )


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


@router.put("/plans/{plan_id}", response_model=PlanResponse)
def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    service: SubscriptionService = Depends(_service),
):
    plan = service.update_plan(plan_id, payload)
    if not plan:
        raise HTTPException(status_code=404, detail="plan_not_found")
    return plan


@router.delete("/plans/{plan_id}", response_model=PlanResponse)
def deactivate_plan(plan_id: int, service: SubscriptionService = Depends(_service)):
    """Takes the plan off the shelf. Subscriptions to it keep working."""
    plan = service.deactivate_plan(plan_id)
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


@router.get("/subscriptions/user/{user_id}/entitlement", response_model=EntitlementResponse)
def get_entitlement(user_id: str, service: SubscriptionService = Depends(_service)):
    subscription = service.get_entitlement(user_id)
    if not subscription:
        return EntitlementResponse(user_id=user_id, active=False)
    return EntitlementResponse(
        user_id=user_id,
        active=True,
        subscription_id=subscription.id,
        plan_id=subscription.plan_id,
        access_until=subscription.current_period_end,
        metadata=(subscription.plan.plan_metadata if subscription.plan else None) or {},
    )


@router.post("/subscriptions/{subscription_id}/pause", response_model=SubscriptionResponse)
def pause_subscription(subscription_id: int, service: SubscriptionService = Depends(_service)):
    try:
        subscription = service.pause_subscription(subscription_id)
    except MercadopagoAPIException as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not subscription:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    return subscription


@router.post("/subscriptions/{subscription_id}/resume", response_model=SubscriptionResponse)
def resume_subscription(subscription_id: int, service: SubscriptionService = Depends(_service)):
    try:
        subscription = service.resume_subscription(subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except MercadopagoAPIException as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if not subscription:
        raise HTTPException(status_code=404, detail="subscription_not_found")
    return subscription


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


@router.post("/subscriptions/{subscription_id}/billing-cycles", response_model=BillingCycleResponse)
def create_billing_cycle(
    subscription_id: int,
    data: BillingCycleCreate,
    service: SubscriptionService = Depends(_service),
):
    try:
        return service.create_billing_cycle(subscription_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e.args[0]))


@router.post("/subscriptions/billing-cycles/{cycle_id}/schedule", response_model=BillingCycleResponse)
def schedule_billing_cycle(
    cycle_id: int,
    service: SubscriptionService = Depends(_service),
):
    try:
        cycle = service.update_next_charge_amount(cycle_id)
        if not cycle:
            raise HTTPException(status_code=404, detail="billing_cycle_not_found")
        return cycle
    except MercadopagoAPIException as e:
        raise HTTPException(status_code=e.status_code, detail={"code": e.error_code, "message": e.error_msg})


@router.post("/subscriptions/reconcile-cancellations")
def reconcile_cancellations(service: SubscriptionService = Depends(_service)):
    """Internal scheduled-job endpoint; protected by the service API key."""
    return {"cancelled": [sub.id for sub in service.cancel_due_subscriptions()]}
