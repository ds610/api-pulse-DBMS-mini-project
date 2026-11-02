import psycopg
from pymongo import MongoClient

# --- MongoDB: compute the numbers from the logs ---
col = MongoClient("mongodb://localhost:27017")["apipulse_logs"]["api_logs"]

pipeline = [
    {"$group": {
        "_id": "$service_id",
        "total": {"$sum": 1},
        "failed": {"$sum": {"$cond": [{"$gte": ["$status_code", 400]}, 1, 0]}},
        "avg_ms": {"$avg": "$response_time_ms"},
        "p95_ms": {"$percentile": {
            "input": "$response_time_ms", "p": [0.95], "method": "approximate"}},
    }},
    {"$sort": {"_id": 1}},
]
stats = list(col.aggregate(pipeline))

# --- PostgreSQL: get the service names ---
with psycopg.connect("postgresql://apipulse:apipulse123@localhost:5433/apipulse") as conn:
    names = dict(conn.execute("SELECT service_id, service_name FROM api_services").fetchall())

# --- Print the report ---
print(f"{'Service':<18}{'Total':>7}{'Failed':>8}{'Error %':>9}{'Avg ms':>8}{'p95 ms':>8}")
for s in stats:
    rate = s["failed"] / s["total"] * 100
    print(f"{names[s['_id']]:<18}{s['total']:>7}{s['failed']:>8}"
          f"{rate:>9.2f}{s['avg_ms']:>8.0f}{s['p95_ms'][0]:>8.0f}")