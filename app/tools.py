"""Graph tools the agent can call. Every call is recorded in a trace for explainability.

Docstrings + type hints are what Gemini sees as the tool schema, so keep them clear.
"""
import contextvars
import functools
import re
import time
import uuid

from . import graph

_trace: contextvars.ContextVar = contextvars.ContextVar("trace", default=None)


def start_trace() -> tuple[list, contextvars.Token]:
    trace: list = []
    return trace, _trace.set(trace)


def end_trace(token) -> None:
    _trace.reset(token)


def traced(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            out = fn(*args, **kwargs)
        except Exception as e:  # return the error to the model so it can recover
            out = {"error": str(e)}
        t = _trace.get()
        if t is not None:
            t.append({"tool": fn.__name__, "args": kwargs or list(args), "result": out})
        return out

    return wrapper


def _with_dashboard_details(rows_: list[dict]) -> list[dict]:
    """Attach 'Dashboard (owner: Name)' strings so ownership is never mixed up."""
    owners = {
        r["dashboard"]: r["owner"]
        for r in graph.rows(
            "MATCH (p:Person)-[:OWNS]->(d:Dashboard) RETURN d.name AS dashboard, p.name AS owner"
        )
    }
    for r in rows_:
        r["dashboard_details"] = [f"{d} (owner: {owners.get(d, 'unknown')})" for d in (r.get("dashboards") or [])]
    return rows_


@traced
def list_tables() -> list[dict]:
    """List every table known to the lineage graph."""
    return graph.rows("MATCH (t:Table) RETURN t.name AS table ORDER BY table")


@traced
def column_impact(table: str, column: str) -> list[dict]:
    """Find every saved query that uses table.column, with its owner, the dashboards it powers,
    and the owners of those dashboards. Use this to answer 'what breaks if I rename/drop/change this column?'."""
    return _with_dashboard_details(graph.rows(
        """
        MATCH (c:Column {id:$id})<-[:USES_COLUMN]-(q:Query)
        OPTIONAL MATCH (p:Person)-[:OWNS]->(q)
        OPTIONAL MATCH (d:Dashboard)-[:POWERED_BY]->(q)
        OPTIONAL MATCH (dp:Person)-[:OWNS]->(d)
        RETURN q.id AS query_id, q.title AS title, p.name AS owner,
               collect(DISTINCT d.name) AS dashboards,
               collect(DISTINCT dp.name) AS dashboard_owners
        """,
        {"id": f"{table}.{column}"},
    ))


@traced
def consumers_of_table(table: str) -> list[dict]:
    """Find every query that reads a table, with its owner, the dashboards powered by it,
    and the owners of those dashboards."""
    return _with_dashboard_details(graph.rows(
        """
        MATCH (t:Table {name:$t})<-[:READS]-(q:Query)
        OPTIONAL MATCH (p:Person)-[:OWNS]->(q)
        OPTIONAL MATCH (d:Dashboard)-[:POWERED_BY]->(q)
        OPTIONAL MATCH (dp:Person)-[:OWNS]->(d)
        RETURN q.id AS query_id, q.title AS title, p.name AS owner,
               collect(DISTINCT d.name) AS dashboards,
               collect(DISTINCT dp.name) AS dashboard_owners
        """,
        {"t": table},
    ))


@traced
def find_downstream(table: str, max_depth: int = 4) -> list[dict]:
    """Multi-hop: find every table that is (transitively) derived from `table`,
    with the shortest path and the number of hops."""
    depth = max(1, min(int(max_depth), 6))
    found = graph.rows(
        f"""
        MATCH p=(t:Table {{name:$t}})-[:FEEDS*1..{depth}]->(x:Table)
        RETURN x.name AS table, length(p) AS hops, [n IN nodes(p) | n.name] AS path
        ORDER BY hops
        """,
        {"t": table},
    )
    best: dict[str, dict] = {}
    for r in found:
        if r["table"] not in best:
            best[r["table"]] = r
    return list(best.values())


@traced
def find_upstream(table: str, max_depth: int = 4) -> list[dict]:
    """Multi-hop: find every table that `table` is (transitively) derived from."""
    depth = max(1, min(int(max_depth), 6))
    found = graph.rows(
        f"""
        MATCH p=(x:Table)-[:FEEDS*1..{depth}]->(t:Table {{name:$t}})
        RETURN x.name AS table, length(p) AS hops, [n IN nodes(p) | n.name] AS path
        ORDER BY hops
        """,
        {"t": table},
    )
    best: dict[str, dict] = {}
    for r in found:
        if r["table"] not in best:
            best[r["table"]] = r
    return list(best.values())


@traced
def who_owns(name: str) -> list[dict]:
    """Find the owner of a dashboard, a query id/title, or the people who own queries that write a table."""
    direct = graph.rows(
        """
        MATCH (p:Person)-[:OWNS]->(n)
        WHERE n.name = $n OR n.id = $n OR n.title = $n
        RETURN p.name AS owner, labels(n)[0] AS kind, coalesce(n.name, n.title) AS item
        """,
        {"n": name},
    )
    writers = graph.rows(
        """
        MATCH (p:Person)-[:OWNS]->(q:Query)-[:WRITES]->(t:Table {name:$n})
        RETURN p.name AS owner, 'writer of table' AS kind, q.title AS item
        """,
        {"n": name},
    )
    return direct + writers


@traced
def get_query_sql(query_id: str) -> list[dict]:
    """Return the raw SQL and title of a saved query by id."""
    return graph.rows(
        "MATCH (q:Query {id:$id}) RETURN q.id AS id, q.title AS title, q.sql AS sql",
        {"id": query_id},
    )


_FORBIDDEN = re.compile(r"\b(CREATE|MERGE|DELETE|SET|REMOVE|DROP|CALL|LOAD)\b", re.I)


@traced
def run_cypher(cypher: str) -> list[dict]:
    """Escape hatch: run a READ-ONLY Cypher query against the lineage graph.
    Schema: (Person)-[:OWNS]->(Query|Dashboard); (Dashboard)-[:POWERED_BY]->(Query);
    (Query)-[:READS|WRITES]->(Table); (Query)-[:USES_COLUMN]->(Column);
    (Table)-[:HAS_COLUMN]->(Column); (Table)-[:FEEDS]->(Table).
    Node props: Table.name, Query.id/title/sql, Column.id/name/table, Dashboard.name, Person.name."""
    if _FORBIDDEN.search(cypher):
        return [{"error": "Only read-only Cypher is allowed."}]
    if "limit" not in cypher.lower():
        cypher += " LIMIT 50"
    return graph.rows(cypher)


@traced
def remember_finding(text: str, table: str = "") -> dict:
    """Persist an important conclusion to long-term agent memory so future sessions can reuse it.
    `table` optionally links the finding to a table name."""
    fid = uuid.uuid4().hex[:8]
    graph.write(
        "CREATE (:Finding {id:$id, text:$text, table:$table, ts:$ts})",
        {"id": fid, "text": text, "table": table, "ts": int(time.time())},
        graph=graph.memory_graph(),
    )
    return {"saved": fid}


@traced
def recall_findings(table: str = "") -> list[dict]:
    """Recall previously saved findings from long-term memory. Pass a table name to filter, or '' for the latest ones."""
    if table:
        return graph.rows(
            "MATCH (f:Finding {table:$t}) RETURN f.text AS text, f.ts AS ts ORDER BY ts DESC LIMIT 10",
            {"t": table},
            graph=graph.memory_graph(),
        )
    return graph.rows(
        "MATCH (f:Finding) RETURN f.text AS text, f.table AS table, f.ts AS ts ORDER BY ts DESC LIMIT 10",
        graph=graph.memory_graph(),
    )


@traced
def rank_critical_tables() -> list[dict]:
    """Graph algorithm tool: rank tables by criticality. Runs PageRank over the table lineage
    (FEEDS edges) and also counts how many tables sit downstream of each one.
    A high downstream_tables count means a change there ripples furthest. Use for questions
    like 'which tables are the most critical / risky to change?'."""
    res = graph.get_graph().query(
        "CALL algo.pageRank('Table', 'FEEDS') YIELD node, score "
        "RETURN node.name AS table, score ORDER BY score DESC"
    )
    pagerank = {r[0]: round(float(r[1]), 4) for r in res.result_set}
    downstream = {
        r["table"]: r["downstream_tables"]
        for r in graph.rows(
            """
            MATCH (t:Table)
            OPTIONAL MATCH (t)-[:FEEDS*1..6]->(d:Table)
            RETURN t.name AS table, count(DISTINCT d) AS downstream_tables
            """
        )
    }
    names = set(pagerank) | set(downstream)
    out = [
        {
            "table": n,
            "pagerank": pagerank.get(n, 0.0),
            "downstream_tables": downstream.get(n, 0),
        }
        for n in names
    ]
    out.sort(key=lambda r: (-r["downstream_tables"], -r["pagerank"]))
    return out


@traced
def notify_owner(owner: str, message: str, related: str = "") -> dict:
    """ACTION tool: send an alert to an owner. Stores a (:Notification) node linked to the
    (:Person) in the graph (simulated delivery). `related` is the table/dashboard concerned."""
    nid = uuid.uuid4().hex[:8]
    graph.write(
        "MERGE (p:Person {name:$o}) "
        "CREATE (n:Notification {id:$id, message:$m, related:$r, ts:$ts})-[:FOR]->(p)",
        {"o": owner, "id": nid, "m": message, "r": related, "ts": int(time.time())},
    )
    return {"notified": owner, "notification_id": nid, "message": message}


TOOLS = [
    list_tables,
    column_impact,
    consumers_of_table,
    find_downstream,
    find_upstream,
    who_owns,
    get_query_sql,
    run_cypher,
    recall_findings,
    remember_finding,
    rank_critical_tables,
    notify_owner,
]