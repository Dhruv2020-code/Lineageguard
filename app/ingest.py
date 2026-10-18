"""Parse SQL with sqlglot and load a lineage knowledge graph into FalkorDB.

Graph model
  (:Person)-[:OWNS]->(:Query|:Dashboard)
  (:Dashboard)-[:POWERED_BY]->(:Query)
  (:Query)-[:READS|WRITES]->(:Table)
  (:Query)-[:USES_COLUMN]->(:Column)<-[:HAS_COLUMN]-(:Table)
  (:Table)-[:FEEDS {via}]->(:Table)        # derived from reads -> writes
"""
import json
from pathlib import Path

import sqlglot
from sqlglot import exp

from . import graph

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "workspace.json"


def _target_table(parsed) -> str | None:
    if isinstance(parsed, (exp.Insert, exp.Create)):
        this = parsed.this
        if isinstance(this, exp.Schema):
            this = this.this
        if isinstance(this, exp.Table):
            return this.name
    return None


def analyze(sql: str, schema: dict[str, list[str]]) -> dict:
    """Return the tables read/written and the (table, column) pairs a query touches."""
    parsed = sqlglot.parse_one(sql, read="postgres")
    ctes = {c.alias for c in parsed.find_all(exp.CTE)}
    target = _target_table(parsed)

    alias_map: dict[str, str] = {}
    reads: set[str] = set()
    for t in parsed.find_all(exp.Table):
        if not t.name or t.name in ctes:
            continue
        alias_map[t.alias_or_name] = t.name
        if t.name != target:
            reads.add(t.name)

    cols: set[tuple[str, str]] = set()
    for c in parsed.find_all(exp.Column):
        if not c.name or isinstance(c.this, exp.Star):
            continue
        if c.table:
            tbl = alias_map.get(c.table)
            if tbl:
                cols.add((tbl, c.name))
        else:
            cands = [t for t in reads if c.name in schema.get(t, [])]
            for t in cands:  # ambiguous -> be conservative and count all candidates
                cols.add((t, c.name))

    # SELECT * (but not COUNT(*)) touches every column of every source table
    for s in parsed.find_all(exp.Star):
        if isinstance(s.parent, exp.Count):
            continue
        for t in reads:
            for col in schema.get(t, []):
                cols.add((t, col))

    return {
        "reads": sorted(reads),
        "writes": [target] if target else [],
        "columns": sorted(cols),
    }


def ingest(workspace: dict | None = None) -> dict:
    if workspace is None:
        workspace = json.loads(DATA_FILE.read_text(encoding="utf-8"))

    graph.reset_main_graph()
    graph.ensure_indexes()
    schema = workspace["schema"]

    for table, columns in schema.items():
        graph.write("MERGE (:Table {name:$t})", {"t": table})
        for col in columns:
            graph.write(
                "MATCH (t:Table {name:$t}) "
                "MERGE (c:Column {id:$id}) SET c.name=$c, c.table=$t "
                "MERGE (t)-[:HAS_COLUMN]->(c)",
                {"t": table, "c": col, "id": f"{table}.{col}"},
            )

    stats = {"queries": 0, "tables": len(schema), "edges": 0}

    for item in workspace["queries"]:
        info = analyze(item["sql"], schema)
        graph.write(
            "MERGE (q:Query {id:$id}) SET q.title=$title, q.sql=$sql",
            {"id": item["id"], "title": item["title"], "sql": item["sql"]},
        )
        graph.write(
            "MATCH (q:Query {id:$id}) MERGE (p:Person {name:$o}) MERGE (p)-[:OWNS]->(q)",
            {"id": item["id"], "o": item["owner"]},
        )
        for t in info["reads"]:
            graph.write(
                "MATCH (q:Query {id:$id}) MERGE (t:Table {name:$t}) MERGE (q)-[:READS]->(t)",
                {"id": item["id"], "t": t},
            )
        for t in info["writes"]:
            graph.write(
                "MATCH (q:Query {id:$id}) MERGE (t:Table {name:$t}) MERGE (q)-[:WRITES]->(t)",
                {"id": item["id"], "t": t},
            )
            for r in info["reads"]:
                graph.write(
                    "MATCH (a:Table {name:$r}), (b:Table {name:$w}) MERGE (a)-[f:FEEDS]->(b) SET f.via=$id",
                    {"r": r, "w": t, "id": item["id"]},
                )
                stats["edges"] += 1
        for t, c in info["columns"]:
            graph.write(
                "MATCH (q:Query {id:$id}) MERGE (t:Table {name:$t}) "
                "MERGE (c:Column {id:$cid}) SET c.name=$c, c.table=$t "
                "MERGE (t)-[:HAS_COLUMN]->(c) MERGE (q)-[:USES_COLUMN]->(c)",
                {"id": item["id"], "t": t, "c": c, "cid": f"{t}.{c}"},
            )
        stats["queries"] += 1

    for d in workspace.get("dashboards", []):
        graph.write(
            "MERGE (d:Dashboard {name:$n}) MERGE (p:Person {name:$o}) MERGE (p)-[:OWNS]->(d)",
            {"n": d["name"], "o": d["owner"]},
        )
        for qid in d["queries"]:
            graph.write(
                "MATCH (d:Dashboard {name:$n}), (q:Query {id:$id}) MERGE (d)-[:POWERED_BY]->(q)",
                {"n": d["name"], "id": qid},
            )

    return stats
