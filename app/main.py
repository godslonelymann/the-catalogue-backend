"""FastAPI app. Run: uvicorn app.main:app --port 8000"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db
from app.config import load_settings
from app.routes import alerts, brands, products, wishlist

log = logging.getLogger("app")
settings = load_settings()


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
