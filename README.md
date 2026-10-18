# LineageGuard — "What breaks if I change this?" agent (FalkorDB)

**Track 1: Agents That Act on Connected Data**

An agent that turns a company's SQL queries, tables, dashboards and owners into a
FalkorDB knowledge graph, then uses graph queries as tools to answer change-impact
questions with multi-hop reasoning, explainable traces, and persistent memory.

| Hackathon requirement | How it's covered |
|---|---|
| Acts on connected data | SQL parsed with sqlglot -> lineage graph (tables, columns, queries, dashboards, owners) |
| Multi-hop reasoning | `FEEDS*1..N` traversals: orders -> daily_revenue -> exec_kpis -> dashboard -> owner |
| Graph queries as tools | 10 tools in `app/tools.py` (+ read-only `run_cypher`) |
| Remembers what it learns | `remember_finding` / `recall_findings` in a separate memory graph |
| Shows how it got to each answer | Tool-call trace in the UI + red-highlighted nodes in the graph |

## Run it (Windows PowerShell)

```powershell
# 1. Start FalkorDB (Docker Desktop must be running)
docker compose up -d

# 2. Python env
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Config
copy .env.example .env
# edit .env -> set GEMINI_API_KEY (free key: https://aistudio.google.com/apikey)

# 4. Start the app
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 -> click **Load sample workspace** -> ask a question.
FalkorDB Browser (inspect the graph yourself): http://localhost:3000

macOS/Linux: activate with `source .venv/bin/activate` and use `cp` instead of `copy`.

## Demo questions
1. What breaks if I rename orders.amount?
2. If I drop the refunds table, which dashboards and owners are affected?
3. Who should I notify before changing customers.region?
4. Which tables are upstream of exec_kpis?
5. What did we learn about orders earlier?  (shows persistent memory)

## Structure
```
docker-compose.yml     FalkorDB + Browser
app/config.py          env settings
app/graph.py           FalkorDB helpers
app/ingest.py          sqlglot -> graph loader
app/tools.py           graph tools the agent calls (+ trace)
app/agent.py           Gemini function-calling loop
app/main.py            FastAPI API + static UI
data/workspace.json    sample schema, queries, dashboards, owners
static/index.html      graph view + chat + trace
```

## Bring your own SQL
Edit `data/workspace.json` (schema, queries with owner, dashboards) and click
**Load sample workspace** again.
