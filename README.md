# Fastapi-performance-monitoring


## Here’s what happens, step by step, every time an HTTP request comes into our FastAPI app, and how our custom metrics get updated via the middleware:

1. The Middleware Hook

We registered a middleware on the "http" event:
```python
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    …
```
FastAPI ensures that every incoming request—regardless of path or method—passes through this function before reaching your actual route handler, and then again after the handler finishes. FastAPI is built on Starlette, which implements the ASGI specification. Our app ends up looking like:

```bash
metrics_middleware → other middlewares → router → route handler
```

2. In-Progress Gauge (inprogress_requests)
```python
IN_PROGRESS.inc()
```
- When? Immediately as the request enters the middleware.

- What it measures? The current number of requests being handled concurrently.

- How?

    - .inc() adds 1 to the gauge.

    - Later, after the request completes, .dec() subtracts 1.

- Result: At any given moment, inprogress_requests shows how many requests are “in flight.”

3. Request Timing (http_request_duration_seconds)

```python
start = time.time()
response = await call_next(request)
latency = time.time() - start
REQUEST_LATENCY.labels(
    request.method, request.url.path, response.status_code
).observe(latency)
```
- When?

    1. Capture t_start just before handing control to FastAPI’s routing (call_next).

    2. Capture t_end immediately after the route handler (and any downstream middleware) completes.

- Histogram mechanics:

    - Each .observe(latency) call:

        1. Increments the overall count by 1.

        2. Adds latency to the sum of all observations.

        3. Bumps every histogram bucket whose upper bound ≥ latency.


This lets us ask Prometheus questions like “What’s the 95th-percentile latency of all GET /data requests over the last 5 minutes?”

4. Total Requests Counter (http_requests_total)
```python
REQUEST_COUNT.labels(
    request.method, request.url.path, response.status_code
).inc()
```
- When? Right after measuring latency, once you have the final response.status_code.

- What it measures? A running total of requests, broken down by:

    - method (GET, POST, etc.)

    - endpoint (the path, e.g. /data)

    - http_status (200, 404, 500, etc.)

- How? Each call to .inc() adds exactly 1 to that three-dimensional counter.

- Result: You can chart or alert on things like

    - “Number of POST /data requests returning 500 errors”

    - “Rate of GET /data requests per second”

5. Full Sequence Diagram

```bash
Incoming HTTP request
         │
         ▼
metrics_middleware starts
  ├─ IN_PROGRESS.inc()
  ├─ t_start = time.time()
  └─ response = await call_next(request)
         │
         ▼
  Your actual route handler runs (DB calls, business logic…)
         │
         ▼
Back in metrics_middleware
  ├─ latency = time.time() - t_start
  ├─ REQUEST_COUNT.labels(...).inc()
  ├─ REQUEST_LATENCY.labels(...).observe(latency)
  └─ IN_PROGRESS.dec()
         │
         ▼
Return `response` to client
```

> Middleware a built-in feature of FastAPI (via Starlette) that any function you register with is automatically injected around every single request. We don’t have to “pass” it into each route, FastAPI builds an ASGI middleware stack for you at application startup.

---
## A simple explanation of how default metrics are being measured

The core idea behind Prometheus’s default (process- and platform-level) metrics in our FastAPI app—what they are, how they get exposed, and why it’s done in a “fixed” (automatic) way:

## 1. What the Default Metrics Are

When we install and import the Python Prometheus client, it automatically wires up two “collector” bundles that expose our application’s own runtime stats:

- Process metrics

    `process_cpu_seconds_total`: cumulative CPU time your Python process has used.

    `process_resident_memory_bytes`: how much RAM (RSS) the process is holding.

    `process_virtual_memory_bytes`: total virtual memory size.

    `process_open_fds`, process_max_fds: file-descriptor counts.

    `process_start_time_seconds`: Unix timestamp when the process began.

- Platform (Python runtime) metrics

    `python_gc_objects_collected_total`, `python_gc_collections_total`: how often the garbage collector has run and how many objects it reclaimed.

We’ll see all of these automatically when we hit your /metrics endpoint—no extra code needed beyond importing the client.

## 2. How It Works “Under the Hood”
1. Auto-registration on import

    The first time we import prometheus_client, the library’s startup code creates a default registry and registers its built-in collectors there.

2. Scrape-time data gathering

    When Prometheus scrapes GET /metrics, the client iterates over every registered collector. Each collector runs its small piece of code—reading from /proc, calling os or gc APIs, etc.—to gather the current values.

3. Text output
    
    Those values are then rendered into the standard Prometheus text exposition format and returned to the caller.

Because this all happens only when Prometheus actually scrapes, there’s no background thread or polling cost inside our app—even if we never manually observe those metrics in our code.

> The Prometheus client library auto-registers both the process and platform collectors when it’s imported. We see all of those metrics automatically when we/prometheus hit/hits our /metrics endpoint—no extra code needed beyond importing the client.

---
## An explanation of how DB_QUERIES_TOTAL is counted and DB_QUERY_DURATION is measured conceptually.

Every time our application runs a SQL statement, two things happen in our instrumentation:

1. Recording the start time

In the before_cursor_execute hook we do:
```bash
context._query_start_time = time.time()
```
That calls Python’s wall-clock timer (seconds since the epoch, as a floating-point number) and stashes it on the SQLAlchemy context.

2. Measuring duration & incrementing counters

In the after_cursor_execute hook we do:
```bash
duration = time.time() - context._query_start_time
op = statement.strip().split()[0].lower()
if op in ("select", "insert", "update", "delete"):
    DB_QUERIES_TOTAL.labels(operation=op).inc()
    DB_QUERY_DURATION .labels(operation=op).observe(duration)
```

`duration` is simply the difference between the end timestamp and the start timestamp measured in seconds as a float.
`DB_QUERIES_TOTAL` is a Counter with one time-series per SQL operation.
- Every time you call .inc(), it adds 1 to that operation’s running total.
- Mathematically, if before you’d executed 42 SELECT queries, after .inc() it becomes 43.
`DB_QUERY_DURATION` is a Histogram with one histogram per operation.
- Under the hood it maintains:
    - A sum of all observed durations:
    - A count of observations:
    - A set of cumulative bucket counts. 

- When we call .observe(duration), the library:
    - Increments the sum by duration.

    - Increments the count by 1.

    - Finds every bucket whose upper bound ≥ duration and increments its counter by 1.


Whenever we use SQLAlchemy’s Engine or Session to run any SQL—whether via ORM methods (e.g. session.query(...)) or raw SQL calls (e.g. session.execute(text(...)))—under the hood SQLAlchemy goes through a common “cursor execution” workflow. The event hooks we’ve registered tap directly into that workflow, so we don’t have to sprinkle any special calls in your CRUD functions.

> Exactly—any time our application goes through the SQLAlchemy Engine to run SQL, those two event hooks fire, regardless of which HTTP verb (GET, POST, PUT, DELETE) or part of our code is initiating it.