import time
import re

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)
from sqlalchemy import text, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import crud, models, schemas
from .database import SessionLocal, engine, Base

# Create tables
Base.metadata.create_all(bind=engine)

# ── FastAPI application ──────────────────────────────────────────
app = FastAPI(title="FastAPI Performance Monitoring")

# ── Metrics ───────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "http_status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint", "http_status"],
    buckets=[0.1, 0.3, 0.5, 1, 3, 5],
)
IN_PROGRESS = Gauge("inprogress_requests", "In-progress HTTP requests")

# ── Default process & platform collectors are auto-registered by prometheus_client ──

# ── DB-specific Metrics ───────────────────────────────────────────
DB_QUERIES_TOTAL = Counter(
    "db_queries_total",
    "Total number of database queries executed",
    ["operation"],  # select, insert, update, delete
)
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds",
    "Duration of database queries in seconds",
    ["operation"],
)
DB_POOL_CHECKED_OUT = Gauge(
    "db_pool_checked_out_connections",
    "Number of connections currently checked out from the pool",
)
DB_POOL_IDLE = Gauge(
    "db_pool_idle_connections",
    "Number of idle connections in the pool",
)
DB_POOL_WAITERS = Gauge(
    "db_pool_waiters",
    "Number of threads waiting for a connection",
)

# ── Instrument SQLAlchemy queries for DB metrics ───────────────────
@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.time()

@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    duration = time.time() - context._query_start_time
    op = statement.strip().split()[0].lower()
    if op in ("select", "insert", "update", "delete"):
        DB_QUERIES_TOTAL.labels(operation=op).inc()
        DB_QUERY_DURATION.labels(operation=op).observe(duration)

# ── DB Dependency ────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ── Middleware for HTTP metrics ──────────────────────────────────
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
    # Update pool stats before scraping
    status = engine.pool.status()
    match = re.search(
        r"Connections in use: (\d+).*Free connections: (\d+).*Waiting connections: (\d+)",
        status,
    )
    if match:
        in_use, free, waiting = map(int, match.groups())
        DB_POOL_CHECKED_OUT.set(in_use)
        DB_POOL_IDLE.set(free)
        DB_POOL_WAITERS.set(waiting)

    data = generate_latest()
    return Response(data, media_type=CONTENT_TYPE_LATEST)

# ── Health check ─────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "reachable"}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "database": "unreachable", "detail": str(e)},
        )
