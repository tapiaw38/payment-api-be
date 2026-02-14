from fastapi import APIRouter

from .mercadopago import router as mercadopago_router
from .payment_methods import router as payment_methods_router
from .payments import router as payments_router
from .subscriptions import router as subscriptions_router
from .webhooks import router as webhooks_router


router = APIRouter()

router.include_router(
    mercadopago_router,
    prefix="/mercadopago",
)
router.include_router(
    payments_router,
    prefix="/payments",
)
router.include_router(
    payment_methods_router,
    prefix="/payment-methods",
)
router.include_router(
    subscriptions_router,
    prefix="/subscriptions",
)
router.include_router(
    webhooks_router,
    prefix="/webhooks",
)
