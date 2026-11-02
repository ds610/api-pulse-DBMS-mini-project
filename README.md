# API Pulse: Centralized API Monitoring & Analytics Platform

API Pulse is a hybrid DBMS platform designed to monitor, track, analyze, and discover third-party APIs (Stripe, Twilio, OpenAI, etc.) in a unified dashboard.

---

## 🏛️ System Architecture

API Pulse utilizes **Polyglot Persistence**:

| Database | Role | Why It Fits |
| :--- | :--- | :--- |
| **PostgreSQL 16 + pgvector** | Relational data & Embeddings | ACID guarantees, foreign keys, triggers, views, and cosine similarity search (`HNSW` index). |
| **MongoDB 7** | High-volume Request Logs | Fast append-only writes, flexible schema, $percentile analytics without loading the relational DB. |
| **Python (FastAPI)** | Gateway & Cross-Database Orchestration | Combines relational thresholds with high-volume metrics for analytics, alerting, and proxy routing. |

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Docker Desktop with WSL 2 backend running
- Python 3.11+
- Git

### 2. Setup Environment
```powershell
# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Launch Databases via Docker
```powershell
docker compose up -d
```
- **PostgreSQL**: `localhost:5433` (User: `apipulse`, Password: `apipulse123`, DB: `apipulse`)
- **MongoDB**: `localhost:27017` (DB: `apipulse_logs`)

### 4. Initialize Database Schema & Starter Data
Run `schema.sql` and `triggers_views_data.sql` in DBeaver or via `psql`.

### 5. Run Database Scripts
```powershell
# 1. Generate 12,000 synthetic request logs in MongoDB
python generate_logs.py

# 2. View cross-database performance report in terminal
python analytics.py

# 3. Test semantic search using pgvector and sentence embeddings
python semantic_search.py

# 4. Run the cross-database alert engine
python alerts.py
```

### 6. Launch the FastAPI Gateway & Web Dashboard
```powershell
uvicorn main:app --reload --port 8000
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser to access:
- **Interactive Analytics & Quota Bars**: Real-time charts for latency (avg vs p95) and traffic volume.
- **Semantic API Discovery**: Live natural language search across registered APIs using pgvector.
- **Alert Engine**: View configured alert rules and triggered events with an on-demand evaluation trigger.
- **Live Gateway Proxy**: Dispatch requests through `/proxy/{service_id}/{path}` and watch MongoDB logs record in real time.

---

## 📂 Project Structure
```
api-pulse/
├── docker-compose.yml        # PostgreSQL (pgvector) + MongoDB containers
├── schema.sql                # 10 relational tables, indexes, constraints
├── triggers_views_data.sql   # Trigger, service_overview view, sample data
├── generate_logs.py          # Synthetic log generator (12,000 logs)
├── analytics.py              # Cross-database performance aggregation
├── semantic_search.py        # Semantic API discovery using pgvector
├── alerts.py                 # Cross-database alert detection & event logging
├── main.py                   # FastAPI app, API endpoints, reverse proxy
├── templates/
│   └── index.html            # Web dashboard (Tailwind CSS + Chart.js)
├── requirements.txt          # Python dependencies
└── .gitignore                # Ignored files (venv, caches, env)
```
