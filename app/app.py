"""
RTP (Real-Time Payments) Status Service
-----------------------------------------
Simulates a payment/transaction status lookup service.

- SQLite acts as our "slow" database (simulated with an artificial delay).
- Redis (added in the next step) will cache repeated lookups.
- /health is used by Kubernetes liveness/readiness probes later.
"""

import os
import time
import json
import sqlite3
import random
import uuid
import redis
from flask import Flask, jsonify, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/transactions.db")

REDIS_HOST = os.environ.get("REDIS_HOST", "redis-service")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", 60))

redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

# --- Prometheus metrics ---
# Counter: a number that only ever goes UP (total requests, total hits).
# Histogram: buckets of observed values, used here for response time
# distribution (lets Grafana show percentiles like p95 latency).
STATUS_REQUESTS = Counter(
    "status_requests_total",
    "Total requests to /status/<id>",
    ["source"],  # labeled by "cache" or "database"
)
STATUS_LATENCY = Histogram(
    "status_request_latency_seconds",
    "Latency of /status/<id> requests",
    ["source"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create the transactions table and seed some sample data."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL
        )
        """
    )
    # Seed a few known transactions so you have IDs to test with.
    sample = conn.execute("SELECT COUNT(*) as c FROM transactions").fetchone()
    if sample["c"] == 0:
        statuses = ["SUCCESS", "PENDING", "FAILED"]
        rows = [
            (str(uuid.uuid4()), random.choice(statuses), round(random.uniform(10, 5000), 2), "INR")
            for _ in range(20)
        ]
        conn.executemany(
            "INSERT INTO transactions (id, status, amount, currency) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        # Print one ID to the logs so you can test easily.
        print(f"[seed] Sample transaction ID for testing: {rows[0][0]}")
    conn.close()


@app.route("/health")
def health():
    """Used by Kubernetes readiness/liveness probes."""
    return jsonify({"status": "ok"}), 200


@app.route("/status/<txn_id>")
def get_status(txn_id):
    """
    Look up a transaction's status using the cache-aside pattern:
      1. Check Redis first.
         - HIT  -> return immediately (fast path).
         - MISS -> fall through to step 2.
      2. Query the database (slow path), then store the result in
         Redis with a TTL, so the NEXT request for this same id is a
         cache hit.
    """
    start = time.time()
    cache_key = f"txn_status:{txn_id}"

    # --- 1. Try the cache first ---
    try:
        cached = redis_client.get(cache_key)
    except redis.exceptions.RedisError as e:
        # If Redis itself is down/unreachable, don't crash the whole
        # request — just fall back to the database (degraded mode).
        cached = None
        print(f"[redis] unavailable, falling back to db: {e}")

    if cached is not None:
        data = json.loads(cached)
        elapsed = time.time() - start
        data["source"] = "cache"
        data["response_time_ms"] = round(elapsed * 1000, 2)
        STATUS_REQUESTS.labels(source="cache").inc()
        STATUS_LATENCY.labels(source="cache").observe(elapsed)
        return jsonify(data)

    # --- 2. Cache miss: go to the database (slow path) ---
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM transactions WHERE id = ?", (txn_id,)
    ).fetchone()
    conn.close()

    # Simulate a slow database (network latency, disk I/O, joins, etc.)
    time.sleep(0.2)

    if row is None:
        return jsonify({"error": "transaction not found"}), 404

    result = {
        "id": row["id"],
        "status": row["status"],
        "amount": row["amount"],
        "currency": row["currency"],
    }

    # Populate the cache for next time. TTL means this expires on its
    # own after CACHE_TTL_SECONDS, so we're never stuck serving stale
    # data forever.
    try:
        redis_client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))
    except redis.exceptions.RedisError as e:
        print(f"[redis] unable to populate cache: {e}")

    elapsed = time.time() - start
    result["source"] = "database"
    result["response_time_ms"] = round(elapsed * 1000, 2)
    STATUS_REQUESTS.labels(source="database").inc()
    STATUS_LATENCY.labels(source="database").observe(elapsed)
    return jsonify(result)


@app.route("/metrics")
def metrics():
    """Prometheus scrapes this endpoint to pull our custom metrics."""
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/txns")
def list_txns():
    """Convenience endpoint: lists a few transaction IDs to test with."""
    conn = get_db()
    rows = conn.execute("SELECT id, status FROM transactions LIMIT 10").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# Initialize the DB at import time so this also runs correctly under
# gunicorn (which never executes the __main__ block below).
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)