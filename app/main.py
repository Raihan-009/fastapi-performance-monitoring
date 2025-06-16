import time
from fastapi import FastAPI, Depends, HTTPException, Request, Response

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

from . import crud, models, schemas
from .database import SessionLocal, engine, Base

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="FastAPI Prometheus Demo")

# ── Metrics ───────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "http_status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint", "http_status"],
    buckets=[0.1, 0.3, 0.5, 1, 3, 5]
)
IN_PROGRESS = Gauge("inprogress_requests", "In-progress HTTP requests")

# ── Register default process & platform collectors ────────────────

# ── DB Dependency ────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ── Middleware for metrics ──────────────────────────────────────
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    IN_PROGRESS.inc()
    start = time.time()
    response = await call_next(request)
    latency = time.time() - start

    REQUEST_COUNT.labels(
        request.method, request.url.path, response.status_code
    ).inc()
    REQUEST_LATENCY.labels(
        request.method, request.url.path, response.status_code
    ).observe(latency)

    IN_PROGRESS.dec()
    return response


# ── CRUD Endpoints ──────────────────────────────────────────────
@app.post("/data", response_model=schemas.UserData, status_code=201)
def create_data(data: schemas.UserDataCreate, db: Session = Depends(get_db)):
    return crud.create_user_data(db, data)

@app.get("/data", response_model=list[schemas.UserData])
def read_data(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud.get_user_data(db, skip, limit)

@app.put("/data/{item_id}", response_model=schemas.UserData)
def update_data(item_id: int, data: schemas.UserDataCreate, db: Session = Depends(get_db)):
    updated = crud.update_user_data(db, item_id, data)
    if not updated:
        raise HTTPException(404, "Item not found")
    return updated

@app.delete("/data/{item_id}", response_model=schemas.UserData)
def delete_data(item_id: int, db: Session = Depends(get_db)):
    deleted = crud.delete_user_data(db, item_id)
    if not deleted:
        raise HTTPException(404, "Item not found")
    return deleted

# ── Prometheus metrics endpoint ─────────────────────────────────
@app.get("/metrics")
def metrics():
    data = generate_latest()
    return Response(data, media_type=CONTENT_TYPE_LATEST)

# ── health check ─────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check(db: Session = Depends(get_db)):
    """
    Simple health endpoint.
    - Checks the app is alive.
    - Verifies DB connectivity by running a trivial query.
    """
    try:
        # run a minimal query to verify DB is up
        db.execute(text("SELECT 1")) # it attempts to execute a raw string SELECT 1, which is no longer allowed in SQLAlchemy v2.0.
        return {"status": "ok", "database": "reachable"}
    except Exception as e:
        # on failure, return 500 with details
        return JSONResponse(
            status_code=500,
            content={"status": "error", "database": "unreachable", "detail": str(e)},
        )
