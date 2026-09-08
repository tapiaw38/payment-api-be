from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from gateways.mercadopago.exceptions import MercadopagoAPIException
from .v1 import routers
from .v1.routers.mercadopago import mercado_pago_api_error_handler


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    contact=settings.CONTACT,
    openapi_url=settings.OPEN_API_URL,
    root_path=settings.ROOT_PATH,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["OPTIONS", "GET", "POST", "DELETE", "PUT", "PATCH"],
    allow_headers=["*"],
)


# Paths that answer without a key. Webhooks are called by Mercado Pago, which
# has no key to send; they authenticate by signature instead.
UNAUTHENTICATED_PREFIXES = ("/api/v1/webhooks",)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if request.url.path.startswith(UNAUTHENTICATED_PREFIXES):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)

    keys = settings.tenant_by_api_key
    if not keys:
        # Fails closed. This used to serve every request when no key was
        # configured, which meant one missing environment variable exposed
        # plan creation and every subscription in the database.
        return JSONResponse(
            status_code=503,
            content={"detail": "payments api keys are not configured"},
        )

    tenant = keys.get(request.headers.get("X-API-Key") or "")
    if not tenant:
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})

    # Handlers read the tenant from here, never from the request: a caller must
    # not be able to name a tenant it does not hold the key for.
    request.state.tenant = tenant
    return await call_next(request)

# routers
app.include_router(routers.router, prefix="/api/v1", tags=["v1"])

# exception handlers
app.add_exception_handler(MercadopagoAPIException, mercado_pago_api_error_handler)


# Route that lists all available routes in a module
@app.get("/{module}/")
def list_routes(module: str):
    module_routes = []
    base_url = f"/api/v1"
    for route in app.routes:
        if (
            route.path.startswith(f"{base_url}/{module}")
            and route.path != f"/{module}/"
        ):
            module_routes.append(route.path)
    return {"module_routes": module_routes}
