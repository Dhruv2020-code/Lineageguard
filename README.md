# LineageGuard: "What breaks if I change this?" agent

**Conference Hackathon 2026 | Track 1: Agents That Act on Connected Data** | Powered by FalkorDB

Repo: https://github.com/Dhruv2020-code/lineageguard

LineageGuard turns a company's SQL queries, tables, dashboards and owners into a FalkorDB
knowledge graph. A Gemini agent answers change-impact questions such as
*"If I drop the refunds table, which dashboards and owners are affected?"* using graph tools,
multi-hop Cypher traversals, a graph algorithm (PageRank), long-term memory, and a visible
trace of how it reached every answer.

## Why a graph
Data teams break dashboards because nobody can see the chain
`orders -> daily_revenue -> exec_kpis -> dashboard -> owner`.
That is a multi-hop relationship question, which is exactly what a graph answers well.
Without FalkorDB the agent has nothing to answer from.

| Track 1 focus | How LineageGuard covers it |
|---|---|
| Multi-hop reasoning | `FEEDS*1..N` traversals across tables, queries, dashboards and owners |
| Graph queries and algorithms as agent tools | 12 tools in `app/tools.py`: lineage, impact, ownership, read-only Cypher, PageRank |
| Actions based on what the graph shows | `notify_owner` records an alert for every affected owner in the graph (delivery is simulated) |
| Explainable results | Every answer shows each tool call and its result; nodes the agent used are highlighted in the graph |
| Memory | `remember_finding` / `recall_findings` store conclusions in a separate FalkorDB graph |

## Graph data model
```
(:Person)-[:OWNS]->(:Query | :Dashboard)
(:Dashboard)-[:POWERED_BY]->(:Query)
(:Query)-[:READS | :WRITES]->(:Table)
(:Query)-[:USES_COLUMN]->(:Column)<-[:HAS_COLUMN]-(:Table)
(:Table)-[:FEEDS {via}]->(:Table)        // derived: tables a query reads -> table it writes
(:Notification)-[:FOR]->(:Person)        // written by the notify_owner action
```
Node properties: `Table.name`, `Column.id/name/table`, `Query.id/title/sql`, `Dashboard.name`,
`Person.name`. Agent memory lives in a second graph (`lineageguard_memory`) with
`(:Finding {id, text, table, ts})`.

SQL is parsed with `sqlglot` (tables read and written, column usage, `SELECT *` expansion)
and loaded by `app/ingest.py`.

## Cypher and graph algorithms the product depends on
Column impact (direct):
```cypher
MATCH (c:Column {id:$id})<-[:USES_COLUMN]-(q:Query)
OPTIONAL MATCH (p:Person)-[:OWNS]->(q)
OPTIONAL MATCH (d:Dashboard)-[:POWERED_BY]->(q)
OPTIONAL MATCH (dp:Person)-[:OWNS]->(d)
RETURN q.id AS query_id, q.title AS title, p.name AS owner,
       collect(DISTINCT d.name) AS dashboards, collect(DISTINCT dp.name) AS dashboard_owners
```
Downstream lineage (multi-hop):
```cypher
MATCH p=(t:Table {name:$t})-[:FEEDS*1..4]->(x:Table)
RETURN x.name AS table, length(p) AS hops, [n IN nodes(p) | n.name] AS path
ORDER BY hops
```
Upstream lineage (multi-hop):
```cypher
MATCH p=(x:Table)-[:FEEDS*1..4]->(t:Table {name:$t})
RETURN x.name AS table, length(p) AS hops, [n IN nodes(p) | n.name] AS path
ORDER BY hops
```
Consumers of a table:
```cypher
MATCH (t:Table {name:$t})<-[:READS]-(q:Query)
OPTIONAL MATCH (p:Person)-[:OWNS]->(q)
OPTIONAL MATCH (d:Dashboard)-[:POWERED_BY]->(q)
RETURN q.id, q.title, p.name AS owner, collect(DISTINCT d.name) AS dashboards
```
Ownership:
```cypher
MATCH (p:Person)-[:OWNS]->(n)
WHERE n.name = $n OR n.id = $n OR n.title = $n
RETURN p.name AS owner, labels(n)[0] AS kind, coalesce(n.name, n.title) AS item
```
Graph algorithm, PageRank over table lineage (combined with downstream counts):
```cypher
CALL algo.pageRank('Table', 'FEEDS') YIELD node, score
RETURN node.name AS table, score ORDER BY score DESC
```
Action and memory writes:
```cypher
MERGE (p:Person {name:$o}) CREATE (:Notification {id:$id, message:$m, related:$r, ts:$ts})-[:FOR]->(p)
CREATE (:Finding {id:$id, text:$text, table:$table, ts:$ts})      // in the memory graph
```

## Setup

### 1. Get the code
```powershell
git clone https://github.com/Dhruv2020-code/lineageguard.git
cd lineageguard
```

### 2. FalkorDB (pick one)
**Local Docker**
```powershell
docker compose up -d        # FalkorDB on :6379, Browser UI on :3000
```
**FalkorDB Cloud (free tier):** create an instance at https://app.falkordb.cloud and note its host, port and password.

### 3. Python environment
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
macOS/Linux: activate with `source .venv/bin/activate`.

### 4. Configure
```powershell
copy .env.example .env
```
Edit `.env`:
```
GEMINI_API_KEY=your_key            # free key: https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-2.5-flash
FALKORDB_HOST=localhost            # or your Cloud host
FALKORDB_PORT=6379                 # or your Cloud port
FALKORDB_USERNAME=                 # "falkordb" for Cloud, empty for local Docker
FALKORDB_PASSWORD=                 # empty for local Docker
GRAPH_NAME=lineageguard
MEMORY_GRAPH_NAME=lineageguard_memory
```

### 5. Run
```powershell
python -m uvicorn app.main:app --reload --port 8000
```
Open http://localhost:8000, click **Load sample workspace**, then ask a question.
(FalkorDB Browser for local Docker: http://localhost:3000.)

An alternative Streamlit UI is also included: `streamlit run streamlit_app.py`.

If the Gemini free quota is exhausted, the app falls back to an offline mode that runs the
same graph tools without the LLM and says so in the answer.

## Demo questions
1. If I drop the refunds table, which dashboards and owners are affected?
2. What breaks if I rename orders.amount?
3. Which tables are most critical to change?
4. Which tables are upstream of exec_kpis?
5. What did we learn about orders earlier? (shows persistent memory)

## Project structure
```
app/config.py          settings from .env
app/graph.py           FalkorDB helpers
app/ingest.py          sqlglot -> lineage graph loader
app/tools.py           graph tools for the agent (+ trace)
app/agent.py           Gemini function-calling loop + offline fallback
app/main.py            FastAPI API
static/index.html      graph view + chat + tool-call trace
streamlit_app.py       alternative Streamlit UI
data/workspace.json    sample schema, queries, dashboards, owners (synthetic)
docker-compose.yml     local FalkorDB
```

## Data
All data in `data/workspace.json` is synthetic. To use your own SQL, edit that file
(schema, queries with owners, dashboards) and click **Load sample workspace** again.

## Tech stack
FalkorDB, Cypher, Python, FastAPI, sqlglot, Gemini 2.5 Flash (function calling), vis-network.

## AI assistance disclosure
This project was built with help from an AI coding assistant (Claude by Anthropic). I reviewed
and tested the code and can explain the graph model, the Cypher queries and the architecture.
