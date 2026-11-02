import psycopg
from pymongo import MongoClient
from datetime import datetime, timezone

# 1. Connect to both databases
pg_conn = psycopg.connect("postgresql://apipulse:apipulse123@localhost:5433/apipulse", autocommit=True)
mongo_client = MongoClient("mongodb://localhost:27017")
logs_col = mongo_client["apipulse_logs"]["api_logs"]

print("--- Running API Pulse Alert Engine ---")

# 2. Fetch active alerts along with service details & usage limits from PostgreSQL
alerts_sql = """
    SELECT a.alert_id, a.service_id, s.service_name, a.metric, a.threshold,
           l.limit_type, l.max_requests
    FROM alerts a
    JOIN api_services s ON s.service_id = a.service_id
    LEFT JOIN usage_limits l ON l.service_id = a.service_id
    WHERE a.is_active = TRUE;
"""
active_alerts = pg_conn.execute(alerts_sql).fetchall()

# 3. Fetch aggregated stats per service from MongoDB
pipeline = [
    {"$group": {
        "_id": "$service_id",
        "total": {"$sum": 1},
        "failed": {"$sum": {"$cond": [{"$gte": ["$status_code", 400]}, 1, 0]}},
        "avg_ms": {"$avg": "$response_time_ms"},
        "p95_ms": {"$percentile": {
            "input": "$response_time_ms", "p": [0.95], "method": "approximate"}},
    }}
]
stats_by_service = {s["_id"]: s for s in logs_col.aggregate(pipeline)}

# 4. Evaluate each alert rule against the MongoDB metrics
triggered_count = 0
for alert_id, service_id, service_name, metric, threshold, limit_type, max_req in active_alerts:
    stats = stats_by_service.get(service_id)
    if not stats:
        continue

    total = stats["total"]
    failed = stats["failed"]
    avg_ms = stats["avg_ms"]
    p95_ms = stats["p95_ms"][0] if stats["p95_ms"] else 0
    err_rate = (failed / total * 100) if total > 0 else 0

    breached = False
    message = ""

    if metric == "usage_percent":
        if max_req and max_req > 0:
            usage_pct = (total / max_req) * 100
            if usage_pct >= float(threshold):
                breached = True
                message = (f"Usage Alert: '{service_name}' consumed {total} requests "
                           f"({usage_pct:.1f}% of {limit_type} limit {max_req}, threshold {threshold}%).")
    elif metric == "error_rate":
        if err_rate >= float(threshold):
            breached = True
            message = (f"Error Rate Alert: '{service_name}' error rate reached "
                       f"{err_rate:.2f}% ({failed}/{total} failed, threshold {threshold}%).")
    elif metric == "latency":
        # Check if either avg or p95 breached the latency threshold
        if avg_ms >= float(threshold) or p95_ms >= float(threshold):
            breached = True
            message = (f"Latency Alert: '{service_name}' response time exceeded threshold {threshold}ms "
                       f"(Avg: {avg_ms:.0f}ms, p95: {p95_ms:.0f}ms).")

    if breached:
        triggered_count += 1
        pg_conn.execute(
            "INSERT INTO alert_events (alert_id, message) VALUES (%s, %s)",
            (alert_id, message)
        )
        print(f"[TRIGGERED] Alert #{alert_id} for {service_name}:")
        print(f"            {message}")
    else:
        print(f"[OK] Alert #{alert_id} for {service_name} ({metric} threshold {threshold}): Within safe limits.")

print(f"\nAlert run finished. Total triggered events logged: {triggered_count}")

# 5. Display the latest events from PostgreSQL alert_events table
print("\n--- Recent Alert Events in PostgreSQL ---")
recent_events = pg_conn.execute("""
    SELECT e.event_id, s.service_name, e.triggered_at, e.message
    FROM alert_events e
    JOIN alerts a ON a.alert_id = e.alert_id
    JOIN api_services s ON s.service_id = a.service_id
    ORDER BY e.triggered_at DESC
    LIMIT 5;
""").fetchall()

for eid, sname, tat, msg in recent_events:
    print(f"[{tat.strftime('%Y-%m-%d %H:%M:%S')}] Event #{eid} ({sname}): {msg}")

pg_conn.close()

