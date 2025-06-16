# Fastapi-performance-monitoring


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