import random, uuid
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient, ASCENDING, DESCENDING

client = MongoClient("mongodb://localhost:27017")
col = client["apipulse_logs"]["api_logs"]
col.drop()  # start fresh so re-running never duplicates

# service_id -> endpoints (matches the PostgreSQL sample data)
endpoints = {
    1: [("/v1/charges", "POST"), ("/v1/customers", "GET")],
    2: [("/Messages", "POST")],
    3: [("/v1/chat/completions", "POST")],
}
base_latency_ms = {1: 180, 2: 120, 3: 900}   # typical speed per service
error_chance    = {1: 0.04, 2: 0.08, 3: 0.06}

now = datetime.now(timezone.utc)
docs = []
for _ in range(12000):
    sid = random.choices([1, 2, 3], weights=[3, 2, 1])[0]
    path, method = random.choice(endpoints[sid])
    ts = now - timedelta(seconds=random.randint(0, 30 * 24 * 3600))

    if random.random() < error_chance[sid]:
        status = random.choice([400, 401, 429, 500, 502, 503])
    else:
        status = random.choice([200, 200, 200, 201])

    rt = int(random.lognormvariate(0, 0.5) * base_latency_ms[sid])
    if status >= 500:
        rt *= 3                      # server errors are slower
    rt = max(rt, 1)

    docs.append({
        "log_id": uuid.uuid4().hex,
        "service_id": sid,
        "endpoint": path,
        "method": method,
        "status_code": status,
        "response_time_ms": rt,
        "timestamp": ts,
    })

col.insert_many(docs)
col.create_index([("service_id", ASCENDING), ("timestamp", DESCENDING)])
print(f"Inserted {col.count_documents({})} logs")