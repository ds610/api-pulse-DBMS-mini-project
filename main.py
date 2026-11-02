import base64
import hashlib
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import psycopg
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from pgvector.psycopg import register_vector
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer

# ---------------------------------------------------------
# Application Initialization
# ---------------------------------------------------------
app = FastAPI(
    title="API Pulse Gateway & Multi-Tenant Dashboard",
    description="Centralized multi-provider API monitoring, gateway, and semantic discovery with RBAC.",
    version="1.1.0"
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

# ---------------------------------------------------------
# Cryptography: AES-128 Fernet Symmetric Encryption for Keys
# ---------------------------------------------------------
SECRET_SALT = b"api-pulse-demo-secret-key-32b!!"
MASTER_KEY = base64.urlsafe_b64encode(hashlib.sha256(SECRET_SALT).digest())
fernet = Fernet(MASTER_KEY)

def encrypt_key(plain_key: str) -> str:
    return fernet.encrypt(plain_key.encode()).decode()

def decrypt_key(cipher_text: str) -> str:
    try:
        return fernet.decrypt(cipher_text.encode()).decode()
    except Exception:
        return "[Decryption Error]"

def mask_key(plain_key: str) -> str:
    if len(plain_key) <= 8:
        return "••••••••"
    return f"{plain_key[:4]}••••••••{plain_key[-4:]}"

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
# User & Tenant Management Endpoints
# ---------------------------------------------------------
@app.get("/api/users")
async def get_users():
    with get_pg_conn() as conn:
        users = conn.execute("""
            SELECT u.user_id, u.name, u.email, r.role_name, p.plan_name,
                   p.max_services, p.max_requests_per_month
            FROM users u
            JOIN roles r ON r.role_id = u.role_id
            LEFT JOIN user_subscriptions us ON us.user_id = u.user_id AND us.status = 'active'
            LEFT JOIN plans p ON p.plan_id = us.plan_id
            ORDER BY u.user_id;
        """).fetchall()

    return [
        {
            "user_id": u[0],
            "name": u[1],
            "email": u[2],
            "role": u[3],
            "plan_name": u[4] or "Free",
            "max_services": u[5] or 2,
            "max_requests": u[6] or 1000
        }
        for u in users
    ]

@app.post("/api/users/{user_id}/upgrade")
async def upgrade_user_plan(user_id: int):
    with get_pg_conn() as conn:
        # Check current plan
        curr = conn.execute("""
            SELECT p.plan_name FROM user_subscriptions us
            JOIN plans p ON p.plan_id = us.plan_id
            WHERE us.user_id = %s AND us.status = 'active';
        """, (user_id,)).fetchone()

        target_plan = "Pro" if not curr or curr[0] == "Free" else "Enterprise"

        conn.execute("""
            UPDATE user_subscriptions
            SET plan_id = (SELECT plan_id FROM plans WHERE plan_name = %s)
            WHERE user_id = %s AND status = 'active';
        """, (target_plan, user_id))

    return {"status": "success", "new_plan": target_plan}

# ---------------------------------------------------------
# API Endpoints: Overview & Metrics (Tenant-aware)
# ---------------------------------------------------------
@app.get("/api/overview")
async def get_overview(user_id: Optional[str] = None):
    is_user_scoped = user_id and user_id != "all" and user_id.isdigit()
    uid = int(user_id) if is_user_scoped else None

    with get_pg_conn() as conn:
        if uid:
            services_rows = conn.execute("""
                SELECT s.service_id, s.service_name, s.provider, u.name AS owner,
                       p.plan_name, l.limit_type, l.max_requests
                FROM api_services s
                JOIN users u ON u.user_id = s.user_id
                JOIN user_subscriptions us ON us.user_id = u.user_id AND us.status = 'active'
                JOIN plans p ON p.plan_id = us.plan_id
                LEFT JOIN usage_limits l ON l.service_id = s.service_id
                WHERE s.user_id = %s
                ORDER BY s.service_id;
            """, (uid,)).fetchall()

            # Get user subscription limits
            sub_info = conn.execute("""
                SELECT p.plan_name, p.max_services, p.max_requests_per_month
                FROM user_subscriptions us
                JOIN plans p ON p.plan_id = us.plan_id
                WHERE us.user_id = %s AND us.status = 'active';
            """, (uid,)).fetchone()
        else:
            services_rows = conn.execute("""
                SELECT service_id, service_name, provider, owner, plan_name, limit_type, max_requests
                FROM service_overview
                ORDER BY service_id;
            """).fetchall()
            sub_info = None

    service_ids = [r[0] for r in services_rows]

    # Calculate MongoDB metrics
    if uid:
        match_query = {"service_id": {"$in": service_ids}} if service_ids else {"service_id": -1}
        total_logs = logs_col.count_documents(match_query)
        failed_logs = logs_col.count_documents({**match_query, "status_code": {"$gte": 400}})
    else:
        total_logs = logs_col.count_documents({})
        failed_logs = logs_col.count_documents({"status_code": {"$gte": 400}})

    avg_error_rate = (failed_logs / total_logs * 100) if total_logs > 0 else 0.0

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

    response = {
        "total_requests": total_logs,
        "failed_requests": failed_logs,
        "avg_error_rate": avg_error_rate,
        "services": services
    }

    if sub_info:
        response["subscription"] = {
            "plan_name": sub_info[0],
            "max_services": sub_info[1],
            "max_requests": sub_info[2],
            "current_services_count": len(services)
        }

    return response

@app.get("/api/analytics")
async def get_analytics(user_id: Optional[str] = None):
    is_user_scoped = user_id and user_id != "all" and user_id.isdigit()
    uid = int(user_id) if is_user_scoped else None

    with get_pg_conn() as conn:
        if uid:
            names = dict(conn.execute(
                "SELECT service_id, service_name FROM api_services WHERE user_id = %s",
                (uid,)
            ).fetchall())
        else:
            names = dict(conn.execute("SELECT service_id, service_name FROM api_services").fetchall())

    sids = list(names.keys())
    if uid and not sids:
        return []

    pipeline = []
    if sids:
        pipeline.append({"$match": {"service_id": {"$in": sids}}})

    pipeline.extend([
        {"$group": {
            "_id": "$service_id",
            "total": {"$sum": 1},
            "failed": {"$sum": {"$cond": [{"$gte": ["$status_code", 400]}, 1, 0]}},
            "avg_ms": {"$avg": "$response_time_ms"},
            "p95_ms": {"$percentile": {
                "input": "$response_time_ms", "p": [0.95], "method": "approximate"}},
        }},
        {"$sort": {"_id": 1}},
    ])

    raw_stats = list(logs_col.aggregate(pipeline))

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
# Consumer API Registration
# ---------------------------------------------------------
class CreateServicePayload(BaseModel):
    user_id: int
    service_name: str
    provider: str
    base_url: str
    description: str
    api_key: Optional[str] = None
    monthly_limit: int = 1000
    endpoint_path: Optional[str] = "/v1"
    endpoint_method: Optional[str] = "GET"

@app.post("/api/services")
async def create_service(payload: CreateServicePayload):
    with get_pg_conn() as conn:
        # 1. Enforce Subscription Quotas
        sub = conn.execute("""
            SELECT p.plan_name, p.max_services
            FROM user_subscriptions us
            JOIN plans p ON p.plan_id = us.plan_id
            WHERE us.user_id = %s AND us.status = 'active';
        """, (payload.user_id,)).fetchone()

        if sub:
            plan_name, max_allowed = sub[0], sub[1]
            current_count = conn.execute(
                "SELECT COUNT(*) FROM api_services WHERE user_id = %s",
                (payload.user_id,)
            ).fetchone()[0]

            if current_count >= max_allowed:
                raise HTTPException(
                    status_code=403,
                    detail=f"Plan limit reached! Your '{plan_name}' tier allows a maximum of {max_allowed} services. Upgrade to Pro to add more."
                )

        # 2. Compute vector embedding for semantic search
        model = get_model()
        vector = model.encode(payload.description).tolist()

        # 3. Insert into api_services
        new_sid = conn.execute("""
            INSERT INTO api_services (user_id, service_name, provider, base_url, description, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING service_id;
        """, (payload.user_id, payload.service_name, payload.provider, payload.base_url, payload.description, vector)).fetchone()[0]

        # 4. Encrypt and store API key in api_credentials if provided
        if payload.api_key:
            enc_key = encrypt_key(payload.api_key.strip())
            conn.execute("""
                INSERT INTO api_credentials (service_id, encrypted_key)
                VALUES (%s, %s);
            """, (new_sid, enc_key))

        # 5. Insert usage limit
        conn.execute("""
            INSERT INTO usage_limits (service_id, limit_type, max_requests)
            VALUES (%s, 'monthly', %s);
        """, (new_sid, payload.monthly_limit))

        # 6. Insert initial endpoint
        if payload.endpoint_path:
            conn.execute("""
                INSERT INTO api_endpoints (service_id, path, http_method)
                VALUES (%s, %s, %s);
            """, (new_sid, payload.endpoint_path, payload.endpoint_method or "GET"))

        # 7. Insert default alert (usage_percent >= 80%)
        conn.execute("""
            INSERT INTO alerts (service_id, metric, threshold)
            VALUES (%s, 'usage_percent', 80.0);
        """, (new_sid,))

    return {
        "status": "success",
        "service_id": new_sid,
        "service_name": payload.service_name,
        "message": "Service registered, credentials encrypted, and semantic embedding indexed successfully!"
    }

# ---------------------------------------------------------
# API Key Vault Endpoints
# ---------------------------------------------------------
@app.get("/api/credentials")
async def get_credentials(user_id: Optional[str] = None):
    is_user_scoped = user_id and user_id != "all" and user_id.isdigit()
    uid = int(user_id) if is_user_scoped else None

    with get_pg_conn() as conn:
        sql = """
            SELECT c.credential_id, s.service_id, s.service_name, s.provider,
                   c.encrypted_key, c.created_at, u.name AS owner
            FROM api_credentials c
            JOIN api_services s ON s.service_id = c.service_id
            JOIN users u ON u.user_id = s.user_id
        """
        if uid:
            sql += " WHERE s.user_id = %s"
            rows = conn.execute(sql + " ORDER BY c.created_at DESC;", (uid,)).fetchall()
        else:
            rows = conn.execute(sql + " ORDER BY c.created_at DESC;").fetchall()

    credentials = []
    for r in rows:
        decrypted = decrypt_key(r[4])
        credentials.append({
            "credential_id": r[0],
            "service_id": r[1],
            "service_name": r[2],
            "provider": r[3],
            "masked_key": mask_key(decrypted),
            "decrypted_key": decrypted,
            "created_at": r[5].isoformat() if r[5] else None,
            "owner": r[6]
        })

    return credentials

@app.delete("/api/credentials/{credential_id}")
async def delete_credential(credential_id: int):
    with get_pg_conn() as conn:
        conn.execute("DELETE FROM api_credentials WHERE credential_id = %s", (credential_id,))
    return {"status": "success", "message": "Credential revoked"}

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
async def get_alerts(user_id: Optional[str] = None):
    is_user_scoped = user_id and user_id != "all" and user_id.isdigit()
    uid = int(user_id) if is_user_scoped else None

    with get_pg_conn() as conn:
        rules_sql = """
            SELECT a.alert_id, a.service_id, s.service_name, a.metric, a.threshold, a.is_active
            FROM alerts a
            JOIN api_services s ON s.service_id = a.service_id
        """
        events_sql = """
            SELECT e.event_id, a.alert_id, s.service_name, e.triggered_at, e.message
            FROM alert_events e
            JOIN alerts a ON a.alert_id = e.alert_id
            JOIN api_services s ON s.service_id = a.service_id
        """

        if uid:
            rules_sql += " WHERE s.user_id = %s ORDER BY a.alert_id;"
            events_sql += " WHERE s.user_id = %s ORDER BY e.triggered_at DESC LIMIT 20;"
            rules_rows = conn.execute(rules_sql, (uid,)).fetchall()
            events_rows = conn.execute(events_sql, (uid,)).fetchall()
        else:
            rules_rows = conn.execute(rules_sql + " ORDER BY a.alert_id;").fetchall()
            events_rows = conn.execute(events_sql + " ORDER BY e.triggered_at DESC LIMIT 20;").fetchall()

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
# API Gateway Reverse Proxy Layer (With Injected Key Header)
# ---------------------------------------------------------
@app.api_route("/proxy/{service_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def gateway_proxy(service_id: int, path: str, request: Request):
    with get_pg_conn() as conn:
        svc = conn.execute(
            "SELECT service_id, service_name, base_url FROM api_services WHERE service_id = %s",
            (service_id,)
        ).fetchone()

        cred = conn.execute(
            "SELECT encrypted_key FROM api_credentials WHERE service_id = %s ORDER BY created_at DESC LIMIT 1",
            (service_id,)
        ).fetchone()

    if not svc:
        raise HTTPException(status_code=404, detail=f"Service ID {service_id} not registered in API Pulse")

    service_name = svc[1]
    base_url = svc[2].rstrip("/")
    target_url = f"{base_url}/{path.lstrip('/')}"

    # Decrypt key if available
    decrypted_key = decrypt_key(cred[0]) if cred else None

    # Forward request & measure latency
    t0 = time.time()
    status_code = 502
    response_body = b""
    content_type = "application/json"

    body_bytes = await request.body()
    query_params = dict(request.query_params)

    headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
    if decrypted_key and "authorization" not in headers:
        headers["Authorization"] = f"Bearer {decrypted_key}"

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

    # Log into MongoDB
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
