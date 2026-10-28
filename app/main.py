"""FastAPI app. Run: uvicorn app.main:app --port 8000"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import load_settings
from app.routes import alerts, brands, products, wishlist

log = logging.getLogger("app")
settings = load_settings()

# Our own loggers (app.*). Uvicorn keeps its own access log; both go to the systemd journal.
logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")

SLOW_REQUEST_S = 1.0


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pool(settings.database_url)
    yield
    db.close_pool()


app = FastAPI(title="The Catalogue API", version="1.0.0", lifespan=lifespan)

# Only the frontend origin(s) may call the API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    # The access log has no timings: flag slow requests so they are easy to find.
    started = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - started
    if elapsed >= SLOW_REQUEST_S:
        log.warning("slow request: %s %s took %.2f s", request.method, request.url.path, elapsed)
    else:
        log.debug("%s %s took %.3f s", request.method, request.url.path, elapsed)
    return response


@app.exception_handler(HTTPException)
async def log_http_error(request: Request, exc: HTTPException):
    # 404 / 409 etc.: the access log only shows the status, so log the reason too.
    log.info("%s %s -> %d: %s", request.method, request.url.path, exc.status_code, exc.detail)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def log_validation_error(request: Request, exc: RequestValidationError):
    fields = ", ".join(".".join(str(part) for part in e["loc"]) for e in exc.errors())
    log.info("%s %s -> 422: invalid %s", request.method, request.url.path, fields)
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Log the details, never send SQL errors or stack traces to the client.
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", tags=["health"])
def health(conn=Depends(db.get_conn)):
    conn.execute("SELECT 1")
    return {"status": "ok"}


app.include_router(brands.router)
app.include_router(products.router)
app.include_router(wishlist.router)
app.include_router(alerts.router)
