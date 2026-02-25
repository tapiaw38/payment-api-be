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


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    # Webhooks are called by MercadoPago, skip API key check
    if request.url.path.startswith("/api/v1/webhooks"):
        return await call_next(request)
    # Skip preflight requests
    if request.method == "OPTIONS":
        return await call_next(request)
    # If no API key is configured, skip check (dev mode without key)
    if not settings.api_key:
        return await call_next(request)
    api_key = request.headers.get("X-API-Key")
    if api_key != settings.api_key:
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
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
