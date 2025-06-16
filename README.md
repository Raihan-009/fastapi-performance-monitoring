# FastAPI Performance Monitoring

A comprehensive guide to implementing custom metrics and monitoring in FastAPI applications using Prometheus.

## Table of Contents

1. [HTTP Request Monitoring](#http-request-monitoring)
2. [Default System Metrics](#default-system-metrics)
3. [Database Performance Monitoring](#database-performance-monitoring)
4. [Implementation Details](#implementation-details)

---

## HTTP Request Monitoring

### Overview

Our FastAPI application implements comprehensive HTTP request monitoring through custom middleware that tracks three key metrics for every incoming request.

### The Middleware Architecture

We registered a middleware on the "http" event that intercepts every request:

```python
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    # Monitoring logic here
```

FastAPI ensures that every incoming request—regardless of path or method—passes through this function before reaching your actual route handler, and then again after the handler finishes. FastAPI is built on Starlette, which implements the ASGI specification. Our app's request flow looks like:

```
metrics_middleware → other middlewares → router → route handler
```

### HTTP Metrics Breakdown

#### 1. In-Progress Requests Gauge (`inprogress_requests`)

```python
IN_PROGRESS.inc()  # On request start
# ... process request ...
IN_PROGRESS.dec()  # On request completion
```

- **When measured**: Immediately as the request enters the middleware
- **What it tracks**: Current number of requests being handled concurrently
- **How it works**: 
  - `.inc()` adds 1 to the gauge when request starts
  - `.dec()` subtracts 1 when request completes
- **Use case**: Monitor application load and detect traffic spikes

#### 2. Request Duration Histogram (`http_request_duration_seconds`)

```python
start = time.time()
response = await call_next(request)
latency = time.time() - start
REQUEST_LATENCY.labels(
    request.method, request.url.path, response.status_code
).observe(latency)
```

- **When measured**: 
  1. Capture `t_start` just before handing control to FastAPI's routing (`call_next`)
  2. Capture `t_end` immediately after the route handler completes
- **Histogram mechanics**: Each `.observe(latency)` call:
  1. Increments the overall count by 1
  2. Adds latency to the sum of all observations
  3. Bumps every histogram bucket whose upper bound ≥ latency
- **Use case**: Answer questions like "What's the 95th-percentile latency of all GET /data requests?"

#### 3. Total Requests Counter (`http_requests_total`)

```python
REQUEST_COUNT.labels(
    request.method, request.url.path, response.status_code
).inc()
```

- **When measured**: After measuring latency, once we have the final `response.status_code`
- **What it tracks**: Running total of requests, broken down by:
  - `method` (GET, POST, etc.)
  - `endpoint` (the path, e.g., `/data`)
  - `http_status` (200, 404, 500, etc.)
- **How it works**: Each `.inc()` call adds exactly 1 to that three-dimensional counter
- **Use case**: Chart or alert on patterns like "Number of POST /data requests returning 500 errors"

### Complete HTTP Request Flow

```
Incoming HTTP request
         │
         ▼
metrics_middleware starts
  ├─ IN_PROGRESS.inc()
  ├─ t_start = time.time()
  └─ response = await call_next(request)
         │
         ▼
  Our actual route handler runs (DB calls, business logic…)
         │
         ▼
Back in metrics_middleware
  ├─ latency = time.time() - t_start
  ├─ REQUEST_COUNT.labels(...).inc()
  ├─ REQUEST_LATENCY.labels(...).observe(latency)
  └─ IN_PROGRESS.dec()
         │
         ▼
Return response to client
```

> **Key Insight**: Middleware is a built-in feature of FastAPI (via Starlette) where any function you register is automatically injected around every single request. We don't have to "pass" it into each route—FastAPI builds an ASGI middleware stack automatically at application startup.

---

## Default System Metrics

### What Are Default Metrics?

When we install and import the Python Prometheus client, it automatically wires up two "collector" bundles that expose our application's runtime stats without any additional configuration.

### Process Metrics

These metrics provide insights into your Python process's resource usage:

- `process_cpu_seconds_total`: Cumulative CPU time your Python process has used
- `process_resident_memory_bytes`: Current RAM (RSS) the process is holding
- `process_virtual_memory_bytes`: Total virtual memory size
- `process_open_fds` / `process_max_fds`: File descriptor counts and limits
- `process_start_time_seconds`: Unix timestamp when the process began

### Platform (Python Runtime) Metrics

These metrics reveal Python-specific runtime behavior:

- `python_gc_objects_collected_total`: Objects reclaimed by garbage collection
- `python_gc_collections_total`: How often the garbage collector has run

### Auto-Registration Mechanism

#### 1. Import-Time Setup
The first time we `import prometheus_client`, the library automatically:
- Creates a default registry
- Registers built-in collectors for process and platform metrics

#### 2. Scrape-Time Data Collection
When Prometheus scrapes `GET /metrics`:
- The client iterates over every registered collector
- Each collector runs its data-gathering code (reading from `/proc`, calling `os` or `gc` APIs)
- Current values are collected in real-time

#### 3. Text Output Generation
Collected values are rendered into standard Prometheus text exposition format and returned.

> **Performance Note**: This happens only when Prometheus actually scrapes—there's no background thread or polling cost inside our app, even if we never manually use these metrics in our code.

### Default Metrics Collection Flow

```
Application startup
  └─ import prometheus_client                 
       ├─ auto-register ProcessCollector in REGISTRY
       └─ auto-register PlatformCollector in REGISTRY

…our app runs, doing DB calls, HTTP middleware, etc…

Prometheus scraper
  GET http://app:8000/metrics
   ↓
FastAPI /metrics endpoint
   └─ generate_latest()
        └─ iterate over all registered collectors:
             • ProcessCollector.collect()   → sample CPU secs, RSS, VM size, fds, start time  
             • PlatformCollector.collect()  → sample Python GC counts, objects, etc  
             • (plus any custom HTTP or DB metrics you've registered)  
   ↓
Render text exposition format
   ↓
Respond 200 + metrics payload → Prometheus ingests
```

> **Key Insight**: The Prometheus client library auto-registers both process and platform collectors when imported. We see all these metrics automatically when Prometheus hits our `/metrics` endpoint—no extra code needed beyond importing the client.

---

## Database Performance Monitoring

### Overview

Our application instruments every SQL query executed through SQLAlchemy, tracking both query counts and execution duration. This happens automatically through SQLAlchemy's event system.

### Database Metrics

#### Query Counter (`db_queries_total`)
- **Type**: Counter with `operation` label (select, insert, update, delete)
- **Purpose**: Track total number of queries by operation type
- **Usage**: Identify query patterns and detect unusual database activity

#### Query Duration (`db_query_duration_seconds`)
- **Type**: Histogram with `operation` label
- **Purpose**: Measure query execution time distribution
- **Usage**: Find slow queries and monitor database performance trends

#### Connection Pool Metrics
- `db_pool_checked_out_connections`: Active connections in use
- `db_pool_idle_connections`: Available connections waiting for use
- `db_pool_waiters`: Threads waiting for a connection

### Instrumentation Mechanics

#### 1. Start Time Recording

In the `before_cursor_execute` hook:
```python
context._query_start_time = time.time()
```

This captures Python's wall-clock timer (seconds since epoch as float) and stores it on the SQLAlchemy context.

#### 2. Duration Measurement & Counter Updates

In the `after_cursor_execute` hook:
```python
duration = time.time() - context._query_start_time
op = statement.strip().split()[0].lower()
if op in ("select", "insert", "update", "delete"):
    DB_QUERIES_TOTAL.labels(operation=op).inc()
    DB_QUERY_DURATION.labels(operation=op).observe(duration)
```

**Counter Logic**: `DB_QUERIES_TOTAL` maintains one time-series per SQL operation. Each `.inc()` call adds 1 to that operation's running total.

**Histogram Logic**: `DB_QUERY_DURATION` maintains per-operation histograms. When calling `.observe(duration)`, the library:
- Increments the sum by `duration`
- Increments the count by 1  
- Updates every bucket whose upper bound ≥ `duration`

### Database Instrumentation Flow

```
Client
  ↓
FastAPI endpoint handler (e.g. POST/GET → crud or session.execute)
  ↓
SQLAlchemy Engine ── before_cursor_execute ────┐
  │                                            │ record start time (t_start)
  └─> DBAPI cursor.execute(SQL, params) ──> PostgreSQL
            ▲                                  │
            │  result rows                   result
            └── after_cursor_execute ──────────┘
                │
                │ duration = now – t_start
                │ op = first_keyword_of(SQL)
                ├─> DB_QUERIES_TOTAL.labels(op).inc()
                └─> DB_QUERY_DURATION.labels(op).observe(duration)
  ↓
Result returned to FastAPI handler
  ↓
HTTP response sent back to Client
```

> **Universal Coverage**: Any time our application goes through the SQLAlchemy Engine to run SQL, these event hooks fire automatically—regardless of HTTP method (GET, POST, PUT, DELETE) or which part of our code initiates the query.

---

## Implementation Details

### Metrics Endpoint

The `/metrics` endpoint serves Prometheus-formatted data and includes real-time connection pool statistics:

```python
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
```

### Key Design Principles

1. **Zero-Code Instrumentation**: HTTP and database metrics are collected automatically through middleware and event hooks
2. **Minimal Performance Impact**: Metrics collection happens only during actual requests and database operations
3. **Comprehensive Coverage**: Every HTTP request and database query is instrumented
4. **Standard Prometheus Format**: All metrics follow Prometheus naming conventions and best practices

### Monitoring Benefits

- **Request Performance**: Track response times, error rates, and throughput
- **Database Performance**: Monitor query patterns, slow queries, and connection pool health  
- **System Health**: Observe CPU usage, memory consumption, and garbage collection
- **Real-time Visibility**: All metrics available instantly via `/metrics` endpoint