# loadtest.py

import asyncio
import random
import time
from httpx import AsyncClient

# ── Configuration ────────────────────────────────────────────────────
API_URL = "https://66dbf2bd6722fdb9097e9d87-lb-426.bm-southeast.lab.poridhi.io"      # our FastAPI base URL
CONCURRENCY = 10                       # number of concurrent “clients”
REQUESTS_PER_CLIENT = 100              # operations per client
OP_WEIGHTS = {                         # relative frequency of each CRUD op
    "create": 2,
    "read":   5,
    "update": 2,
    "delete": 1,
}
METRICS_EVERY = 10                     # fetch /metrics every N ops

# ── Worker Task ─────────────────────────────────────────────────────
async def worker(name: str):
    async with AsyncClient(base_url=API_URL, timeout=10.0) as client:
        for i in range(REQUESTS_PER_CLIENT):
            op = random.choices(
                list(OP_WEIGHTS.keys()),
                weights=list(OP_WEIGHTS.values()),
                k=1
            )[0]

            try:
                if op == "create":
                    payload = {
                        "name": f"user_{name}_{i}",
                        "email": f"{name}_{i}@example.com",
                        "message": "load test"
                    }
                    resp = await client.post("/data", json=payload)
                    # save new item id for possible updates/deletes
                    if resp.status_code == 201:
                        item_id = resp.json()["id"]

                elif op == "read":
                    await client.get("/data")

                elif op == "update":
                    # pick a random existing item
                    r = await client.get("/data")
                    items = r.json()
                    if items:
                        item = random.choice(items)
                        upd = {
                            "name": item["name"] + "_upd",
                            "email": item["email"],
                            "message": "updated"
                        }
                        await client.put(f"/data/{item['id']}", json=upd)

                elif op == "delete":
                    r = await client.get("/data")
                    items = r.json()
                    if items:
                        item = random.choice(items)
                        await client.delete(f"/data/{item['id']}")

                # occasionally scrape the metrics endpoint
                if i % METRICS_EVERY == 0:
                    await client.get("/metrics")

            except Exception as e:
                print(f"[{name}] error during {op}: {e}")

            # small random pause to spread requests out
            await asyncio.sleep(random.uniform(0.01, 0.2))

        print(f"[{name}] done")

# ── Entry Point ─────────────────────────────────────────────────────
async def main():
    start = time.time()
    tasks = [asyncio.create_task(worker(f"client{i}")) for i in range(CONCURRENCY)]
    await asyncio.gather(*tasks)
    duration = time.time() - start
    print(f"Load test completed in {duration:.2f}s")

if __name__ == "__main__":
    asyncio.run(main())
