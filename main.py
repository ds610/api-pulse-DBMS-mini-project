import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pgvector.psycopg import register_vector
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------
# Application Initialization
# ---------------------------------------------------------
app = FastAPI(
    title="API Pulse Gateway & Dashboard",
    description="Centralized multi-provider API monitoring, gateway, and semantic discovery.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database URIs
PG_URI = "postgresql://apipulse:apipulse123@localhost:5433/apipulse"
MONGO_URI = "mongodb://localhost:27017"

# MongoDB connection
mongo_client = MongoClient(MONGO_URI)
logs_col = mongo_client["apipulse_logs"]["api_logs"]

# Lazy-loaded SentenceTransformer model
_model: Optional[SentenceTransformer] = None

def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

def get_pg_conn():
    conn = psycopg.connect(PG_URI, autocommit=True)
    register_vector(conn)
    return conn

# ---------------------------------------------------------
# Web Dashboard View
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    html_path = Path(__file__).parent / "templates" / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard template not found")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

# ---------------------------------------------------------
# API Endpoints: Overview & Metrics
# ---------------------------------------------------------
@app.get("/api/overview")
async def get_overview():
    total_logs = logs_col.count_documents({})
    failed_logs = logs_col.count_documents({"status_code": {"$gte": 400}})
    avg_error_rate = (failed_logs / total_logs * 100) if total_logs > 0 else 0.0

    with get_pg_conn() as conn:
        services_rows = conn.execute("""
            SELECT service_id, service_name, provider, owner, plan_name, limit_type, max_requests
            FROM service_overview
            ORDER BY service_id;
        """).fetchall()

    services = [
        {
            "service_id": r[0],
            "service_name": r[1],
            "provider": r[2],
            "owner": r[3],
            "plan_name": r[4],
            "limit_type": r[5],
            "max_requests": r[6]
        }
        for r in services_rows
    ]

    return {
        "total_requests": total_logs,
        "failed_requests": failed_logs,
        "avg_error_rate": avg_error_rate,
        "services": services
    }

@app.get("/api/analytics")
async def get_analytics():
    # MongoDB aggregation: totals, errors, avg and p95 latency
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
    raw_stats = list(logs_col.aggregate(pipeline))

    with get_pg_conn() as conn:
        names = dict(conn.execute("SELECT service_id, service_name FROM api_services").fetchall())

    analytics = []
    for s in raw_stats:
        sid = s["_id"]
        total = s["total"]
        failed = s["failed"]
        rate = (failed / total * 100) if total > 0 else 0.0
        p95 = s["p95_ms"][0] if s.get("p95_ms") else 0.0
        analytics.append({
            "service_id": sid,
            "service_name": names.get(sid, f"Service #{sid}"),
            "total": total,
            "failed": failed,
            "error_rate": rate,
            "avg_ms": s.get("avg_ms", 0.0),
            "p95_ms": p95
        })

    return analytics

# ---------------------------------------------------------
# API Endpoints: Semantic Search (pgvector)
# ---------------------------------------------------------
@app.get("/api/search")
async def semantic_search(q: str):
    if not q or not q.strip():
        return {"query": q, "results": []}

    model = get_model()
    query_vector = model.encode(q.strip()).tolist()

    with get_pg_conn() as conn:
        rows = conn.execute("""
            SELECT service_id, service_name, provider, description, base_url,
                   (1 - (embedding <=> %s::vector)) AS similarity
            FROM api_services
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT 5;
        """, (query_vector, query_vector)).fetchall()

    results = [
        {
            "service_id": r[0],
            "service_name": r[1],
            "provider": r[2],
            "description": r[3],
            "base_url": r[4],
            "similarity": float(r[5])
        }
        for r in rows
    ]

    return {"query": q, "results": results}

# ---------------------------------------------------------
# API Endpoints: Alerts
# ---------------------------------------------------------
@app.get("/api/alerts")
async def get_alerts():
    with get_pg_conn() as conn:
        rules_rows = conn.execute("""
            SELECT a.alert_id, a.service_id, s.service_name, a.metric, a.threshold, a.is_active
            FROM alerts a
            JOIN api_services s ON s.service_id = a.service_id
            ORDER BY a.alert_id;
        """).fetchall()

        events_rows = conn.execute("""
            SELECT e.event_id, a.alert_id, s.service_name, e.triggered_at, e.message
            FROM alert_events e
            JOIN alerts a ON a.alert_id = e.alert_id
            JOIN api_services s ON s.service_id = a.service_id
            ORDER BY e.triggered_at DESC
            LIMIT 20;
        """).fetchall()

    rules = [
        {
            "alert_id": r[0],
            "service_id": r[1],
            "service_name": r[2],
            "metric": r[3],
            "threshold": float(r[4]),
            "is_active": r[5]
        }
        for r in rules_rows
    ]

    events = [
        {
            "event_id": r[0],
            "alert_id": r[1],
            "service_name": r[2],
            "triggered_at": r[3].isoformat(),
            "message": r[4]
        }
        for r in events_rows
    ]

    return {"rules": rules, "events": events}

@app.post("/api/alerts/run")
async def run_alerts():
    # Evaluate MongoDB stats against PostgreSQL alert rules
    with get_pg_conn() as conn:
        active_alerts = conn.execute("""
            SELECT a.alert_id, a.service_id, s.service_name, a.metric, a.threshold,
                   l.limit_type, l.max_requests
            FROM alerts a
            JOIN api_services s ON s.service_id = a.service_id
            LEFT JOIN usage_limits l ON l.service_id = a.service_id
            WHERE a.is_active = TRUE;
        """).fetchall()

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
            msg = ""

            if metric == "usage_percent":
                if max_req and max_req > 0:
                    usage_pct = (total / max_req) * 100
                    if usage_pct >= float(threshold):
                        breached = True
                        msg = (f"Usage Alert: '{service_name}' consumed {total} requests "
                               f"({usage_pct:.1f}% of {limit_type} limit {max_req}, threshold {threshold}%).")
            elif metric == "error_rate":
                if err_rate >= float(threshold):
                    breached = True
                    msg = (f"Error Rate Alert: '{service_name}' error rate reached "
                           f"{err_rate:.2f}% ({failed}/{total} failed, threshold {threshold}%).")
            elif metric == "latency":
                if avg_ms >= float(threshold) or p95_ms >= float(threshold):
                    breached = True
                    msg = (f"Latency Alert: '{service_name}' response time exceeded threshold {threshold}ms "
                           f"(Avg: {avg_ms:.0f}ms, p95: {p95_ms:.0f}ms).")

            if breached:
                triggered_count += 1
                conn.execute(
                    "INSERT INTO alert_events (alert_id, message) VALUES (%s, %s)",
                    (alert_id, msg)
                )

    return {"status": "success", "triggered_count": triggered_count}

# ---------------------------------------------------------
# API Gateway Reverse Proxy Layer
# ---------------------------------------------------------
@app.api_route("/proxy/{service_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def gateway_proxy(service_id: int, path: str, request: Request):
    # 1. Resolve service in PostgreSQL
    with get_pg_conn() as conn:
        svc = conn.execute(
            "SELECT service_id, service_name, base_url FROM api_services WHERE service_id = %s",
            (service_id,)
        ).fetchone()

    if not svc:
        raise HTTPException(status_code=404, detail=f"Service ID {service_id} not registered in API Pulse")

    service_name = svc[1]
    base_url = svc[2].rstrip("/")
    target_url = f"{base_url}/{path.lstrip('/')}"

    # 2. Forward request through httpx & measure latency
    t0 = time.time()
    status_code = 502
    response_body = b""
    content_type = "application/json"

    body_bytes = await request.body()
    query_params = dict(request.query_params)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            upstream_resp = await client.request(
                method=request.method,
                url=target_url,
                params=query_params,
                content=body_bytes if body_bytes else None,
                headers=headers
            )
            status_code = upstream_resp.status_code
            response_body = upstream_resp.content
            content_type = upstream_resp.headers.get("content-type", "application/json")
    except httpx.RequestError as exc:
        status_code = 504
        response_body = f'{{"error": "Upstream service unreachable", "target": "{target_url}", "detail": "{str(exc)}"}}'.encode()

    latency_ms = max(int((time.time() - t0) * 1000), 1)

    # 3. Log transaction append-only into MongoDB
    log_doc = {
        "log_id": uuid.uuid4().hex,
        "service_id": service_id,
        "endpoint": f"/{path.lstrip('/')}",
        "method": request.method,
        "status_code": status_code,
        "response_time_ms": latency_ms,
        "timestamp": datetime.now(timezone.utc)
    }
    logs_col.insert_one(log_doc)

    # 4. Return response to caller with gateway performance headers
    return Response(
        content=response_body,
        status_code=status_code,
        media_type=content_type,
        headers={
            "X-API-Pulse-Latency-Ms": str(latency_ms),
            "X-API-Pulse-Service": service_name,
            "X-API-Pulse-Logged": "true"
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
